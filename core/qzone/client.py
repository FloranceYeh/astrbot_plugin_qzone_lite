import asyncio
import random
import time
from collections.abc import Callable
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
    QZONE_MSG_INVALID_RESPONSE,
    QZONE_MSG_JSON_PARSE_ERROR,
    QZONE_MSG_NON_OBJECT_RESPONSE,
    QZONE_MSG_PERMISSION_DENIED,
)
from .parser import QzoneParser
from .session import QzoneSession

RequestUrl = str | Callable[[Any], str]
RequestPayload = dict[str, Any] | Callable[[Any], dict[str, Any] | None] | None


class QzoneHttpClient:
    MAX_RETRIES = 2
    _LOGIN_MESSAGE_MARKERS = (
        "请先登录",
        "请登录",
        "重新登录",
        "登录后",
        "登录空间",
        "登录失效",
        "登录态失效",
        "未登录",
        "会话过期",
        "QQ空间登录",
        "qzone login",
        "please login",
        "please log in",
        "login required",
        "not logged in",
        "not login",
        "need login",
        "login",
        "expired",
        "skey",
        "p_skey",
        "g_tk",
    )
    _LOGIN_BODY_MARKERS = (
        "请先登录",
        "请登录",
        "重新登录",
        "登录后",
        "登录空间",
        "登录失效",
        "登录态失效",
        "未登录",
        "会话过期",
        "QQ空间登录",
        "qzone login",
        "please login",
        "please log in",
        "login required",
        "not logged in",
        "not login",
        "need login",
    )
    _LOGIN_PARSE_MESSAGES = {
        QZONE_MSG_INVALID_RESPONSE,
        QZONE_MSG_JSON_PARSE_ERROR,
        QZONE_MSG_NON_OBJECT_RESPONSE,
    }

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
        url: RequestUrl,
        *,
        params: RequestPayload = None,
        data: RequestPayload = None,
        headers: RequestPayload = None,
        timeout: int | None = None,
        retry: int = 0,
    ) -> dict[str, Any]:
        resp, text = await self._request_once(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            timeout=timeout,
        )

        parsed = QzoneParser.parse_response(text)
        meta = parsed.get(QZONE_INTERNAL_META_KEY)
        if not isinstance(meta, dict):
            meta = {}
            parsed[QZONE_INTERNAL_META_KEY] = meta
        meta[QZONE_INTERNAL_HTTP_STATUS_KEY] = resp.status

        if self._is_login_expired_response(resp.status, parsed, text):
            if retry >= self.MAX_RETRIES:
                raise RuntimeError("登录失效，重试失败")
            await self._reset_and_relogin()
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

        if (
            resp.status == HTTP_STATUS_FORBIDDEN
            and parsed.get("code") in (QZONE_CODE_UNKNOWN, None)
        ):
            parsed["code"] = resp.status
            parsed["message"] = QZONE_MSG_PERMISSION_DENIED

        return parsed

    async def request_text(
        self,
        method: str,
        url: RequestUrl,
        *,
        params: RequestPayload = None,
        data: RequestPayload = None,
        headers: RequestPayload = None,
        timeout: int | None = None,
        retry: int = 0,
    ) -> tuple[int, str]:
        resp, text = await self._request_once(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            timeout=timeout,
        )
        parsed = QzoneParser.parse_response(text)

        if self._is_login_expired_response(resp.status, parsed, text):
            if retry >= self.MAX_RETRIES:
                raise RuntimeError("登录失效，重试失败")
            await self._reset_and_relogin()
            return await self.request_text(
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
            return await self.request_text(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
                retry=retry + 1,
            )

        return resp.status, text

    async def _request_once(
        self,
        method: str,
        url: RequestUrl,
        *,
        params: RequestPayload = None,
        data: RequestPayload = None,
        headers: RequestPayload = None,
        timeout: int | None = None,
    ) -> tuple[Any, str]:
        await self._throttle_request()
        ctx = await self.session.get_ctx()
        resolved_url = self._resolve_url(url, ctx)
        resolved_params = self._resolve_payload(params, ctx)
        resolved_data = self._resolve_payload(data, ctx)
        resolved_headers = self._resolve_payload(headers, ctx)
        async with self._session.request(
            method,
            resolved_url,
            params=resolved_params,
            data=resolved_data,
            headers=resolved_headers if resolved_headers is not None else ctx.headers(),
            cookies=ctx.cookies(),
            timeout=timeout,
        ) as resp:
            text = await resp.text()
        return resp, text

    @staticmethod
    def _resolve_url(url: RequestUrl, ctx: Any) -> str:
        return url(ctx) if callable(url) else url

    @staticmethod
    def _resolve_payload(payload: RequestPayload, ctx: Any) -> dict[str, Any] | None:
        if callable(payload):
            payload = payload(ctx)
        if payload is None:
            return None
        return dict(payload)

    @classmethod
    def _contains_marker(cls, text: str, markers: tuple[str, ...]) -> bool:
        if not text:
            return False
        lowered = text.lower()
        return any(marker in text or marker in lowered for marker in markers)

    @classmethod
    def _contains_login_message_marker(cls, text: str) -> bool:
        return cls._contains_marker(text, cls._LOGIN_MESSAGE_MARKERS)

    @classmethod
    def _contains_login_body_marker(cls, text: str) -> bool:
        return cls._contains_marker(text, cls._LOGIN_BODY_MARKERS)

    @staticmethod
    def _response_code(parsed: dict[str, Any]) -> Any:
        for key in ("code", "ret"):
            value = parsed.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                return value
        return QZONE_CODE_UNKNOWN

    @staticmethod
    def _response_message(parsed: dict[str, Any]) -> str:
        payloads = [parsed]
        data = parsed.get("data")
        if isinstance(data, dict):
            payloads.append(data)
        for payload in payloads:
            for key in ("message", "msg"):
                value = payload.get(key)
                if value:
                    return str(value)
        return ""

    @classmethod
    def _is_login_expired_response(
        cls,
        status: int,
        parsed: dict[str, Any],
        text: str,
    ) -> bool:
        if status == HTTP_STATUS_UNAUTHORIZED:
            return True
        code = cls._response_code(parsed)
        if code == QZONE_CODE_LOGIN_EXPIRED:
            return True

        message = cls._response_message(parsed)
        if (
            message not in cls._LOGIN_PARSE_MESSAGES
            and cls._contains_login_message_marker(message)
        ):
            return True

        body = str(text or "")
        if not cls._contains_login_body_marker(body):
            return False

        if status == HTTP_STATUS_FORBIDDEN:
            return True

        if code == QZONE_CODE_UNKNOWN and message in cls._LOGIN_PARSE_MESSAGES:
            return True
        return False

    async def _reset_and_relogin(self) -> None:
        logger.warning("登录失效，正在重置状态并重新登录")
        await self.session.reset_login_state(
            clear_cookies=bool(self.cfg.auto_reset_on_login_expired)
        )
        await self.session.login()

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
