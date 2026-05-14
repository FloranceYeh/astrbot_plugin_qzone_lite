import asyncio
import random
import time
from typing import Any

import aiohttp

from astrbot.api import logger

from ..config import PluginConfigLite
from .constants import (
    HTTP_STATUS_FORBIDDEN,
    HTTP_STATUS_UNAUTHORIZED,
    QZONE_CODE_LOGIN_EXPIRED,
    QZONE_CODE_UNKNOWN,
    QZONE_INTERNAL_HTTP_STATUS_KEY,
    QZONE_INTERNAL_META_KEY,
    QZONE_MSG_PERMISSION_DENIED,
)
from .parser import QzoneParser
from .session import QzoneSession


class QzoneHttpClient:
    MAX_RETRIES = 2

    def __init__(self, session: QzoneSession, config: PluginConfigLite):
        self.cfg = config
        self.session = session
        self._request_lock = asyncio.Lock()
        self._last_request_ts = 0.0
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.cfg.timeout)
        )

    async def close(self):
        await self._session.close()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        retry: int = 0,
    ) -> dict[str, Any]:
        await self._throttle_request()
        ctx = await self.session.get_ctx()
        async with self._session.request(
            method,
            url,
            params=params,
            data=data,
            headers=headers or ctx.headers(),
            cookies=ctx.cookies(),
            timeout=timeout,
        ) as resp:
            text = await resp.text()

        parsed = QzoneParser.parse_response(text)
        meta = parsed.get(QZONE_INTERNAL_META_KEY)
        if not isinstance(meta, dict):
            meta = {}
            parsed[QZONE_INTERNAL_META_KEY] = meta
        meta[QZONE_INTERNAL_HTTP_STATUS_KEY] = resp.status

        if resp.status == HTTP_STATUS_UNAUTHORIZED or parsed.get("code") == QZONE_CODE_LOGIN_EXPIRED:
            if retry >= self.MAX_RETRIES:
                raise RuntimeError("登录失效，重试失败")
            logger.warning("登录失效，重新登录中")
            await self.session.login()
            return await self.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
                retry=retry + 1,
            )

        if resp.status == 429:
            if retry >= self.MAX_RETRIES:
                raise RuntimeError("请求过于频繁，请稍后重试")
            backoff = max(
                1.0,
                self.cfg.request_interval * (2**retry)
                + random.uniform(0, self.cfg.request_jitter),
            )
            logger.warning(f"请求过于频繁，{backoff:.2f}s 后重试")
            await asyncio.sleep(backoff)
            return await self.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
                retry=retry + 1,
            )

        if resp.status == HTTP_STATUS_FORBIDDEN and parsed.get("code") in (QZONE_CODE_UNKNOWN, None):
            parsed["code"] = resp.status
            parsed["message"] = QZONE_MSG_PERMISSION_DENIED

        return parsed

    async def _throttle_request(self) -> None:
        interval = max(0.0, float(self.cfg.request_interval))
        jitter = max(0.0, float(self.cfg.request_jitter))
        if interval <= 0 and jitter <= 0:
            return
        async with self._request_lock:
            now = time.monotonic()
            next_allowed = self._last_request_ts + interval + random.uniform(0, jitter)
            wait_seconds = next_allowed - now
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._last_request_ts = time.monotonic()
