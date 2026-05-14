from collections.abc import Sequence
import asyncio
import ipaddress
from typing import Union
from urllib.parse import urlparse

import aiohttp

from astrbot.api import logger

BytesOrStr = Union[str, bytes]


async def _is_safe_image_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost":
        return False

    try:
        ip = ipaddress.ip_address(host)
        ips = {ip}
    except ValueError:
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, None, type=0)
            ips = {
                ipaddress.ip_address(info[4][0])
                for info in infos
                if info and len(info) > 4 and info[4]
            }
        except Exception:
            return False

    for ip in ips:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


async def download_file(url: str) -> bytes | None:
    if not await _is_safe_image_url(url):
        parsed = urlparse(url)
        safe_target = f"{parsed.scheme}://{parsed.hostname or ''}"
        logger.warning(f"拒绝下载不安全图片 URL: {safe_target}")
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
