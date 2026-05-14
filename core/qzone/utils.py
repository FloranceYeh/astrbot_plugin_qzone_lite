from collections.abc import Sequence
import ipaddress
from typing import Union
from urllib.parse import urlparse

import aiohttp

from astrbot.api import logger

BytesOrStr = Union[str, bytes]


def _is_safe_image_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost":
        return False

    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    except ValueError:
        pass
    return True


async def download_file(url: str) -> bytes | None:
    if not _is_safe_image_url(url):
        logger.warning(f"拒绝下载不安全图片 URL: {url}")
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(url) as response:
                response.raise_for_status()
                return await response.read()
    except Exception as e:
        logger.error(f"图片下载失败: {e}")


async def normalize_images(images: Sequence[BytesOrStr] | None) -> list[bytes]:
    if images is None:
        return []
    cleaned: list[bytes] = []
    for item in images:
        if isinstance(item, bytes):
            cleaned.append(item)
        elif isinstance(item, str):
            file = await download_file(item)
            if file is not None:
                cleaned.append(file)
        else:
            raise TypeError(f"image 必须是 str 或 bytes，收到 {type(item)}")
    return cleaned
