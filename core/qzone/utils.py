from collections.abc import Sequence
from typing import Union

import aiohttp

from astrbot.api import logger

BytesOrStr = Union[str, bytes]


async def download_file(url: str) -> bytes | None:
    url = url.replace("https://", "http://")
    try:
        async with aiohttp.ClientSession() as client:
            response = await client.get(url)
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
