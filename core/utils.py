from __future__ import annotations

from astrbot.core.message.components import At, Image, Reply
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


def get_ats(event: AiocqhttpMessageEvent) -> list[str]:
    ats = [str(seg.qq) for seg in event.get_messages()[1:] if isinstance(seg, At)]
    for arg in event.message_str.split(" "):
        if arg.startswith("@") and arg[1:].isdigit():
            ats.append(arg[1:])
    return ats


def parse_range_from_tokens(tokens: list[str]) -> tuple[int, int, int | None]:
    """在 tokens 中寻找第一个范围 token，返回 (pos, num, idx)。

    - n        → pos=n(允许 0)，num=1
    - s~e      → pos=s-1,num=e-s+1（兼容 s=0 的情况）
    - 未找到    → pos=0,num=1,idx=None
    """

    for i, tok in enumerate(tokens):
        if "~" in tok:
            s, _, e = tok.partition("~")
            if s.isdigit() and e.isdigit():
                s_i = int(s)
                e_i = int(e)
                if e_i < s_i:
                    continue
                if s_i == 0:
                    return 0, e_i - s_i + 1, i
                return s_i - 1, e_i - s_i + 1, i
        elif tok.isdigit():
            n = int(tok)
            return (n - 1 if n > 0 else 0), 1, i
    return 0, 1, None


def parse_range(event: AstrMessageEvent) -> tuple[int, int]:
    parts = event.message_str.strip().split()
    pos, num, _ = parse_range_from_tokens(parts)
    return pos, num


def _extract_image_source(seg: Image) -> str | None:
    candidates: list[str] = []
    for key in ("url", "file", "src", "data_url"):
        value = getattr(seg, key, None)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    raw = getattr(seg, "raw", None)
    if isinstance(raw, dict):
        for key in ("url", "file", "src"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    for value in candidates:
        if value.startswith(("http://", "https://", "base64://", "data:image/")):
            return value
    return None


async def get_image_urls(event: AstrMessageEvent, reply: bool = True) -> list[str]:
    chain = event.get_messages()
    images: list[str] = []
    if reply:
        reply_seg = next((seg for seg in chain if isinstance(seg, Reply)), None)
        if reply_seg and reply_seg.chain:
            for seg in reply_seg.chain:
                if isinstance(seg, Image):
                    source = _extract_image_source(seg)
                    if source:
                        images.append(source)
    for seg in chain:
        if isinstance(seg, Image):
            source = _extract_image_source(seg)
            if source:
                images.append(source)
    return list(dict.fromkeys(images))


def parse_comment_args(event: AiocqhttpMessageEvent) -> tuple[str | None, int, int, str]:
    """解析 `评说说 [@用户] [序号/范围] <内容>`"""

    tokens = event.message_str.strip().split()
    if not tokens:
        return None, 0, 1, ""

    # 去掉命令本身
    tokens = tokens[1:]

    # 目标用户
    at_ids = get_ats(event)
    target_id = at_ids[0] if at_ids else None

    # 去掉 @xxx token（不影响解析范围）
    filtered = [t for t in tokens if not (t.startswith("@") and t[1:].isdigit())]

    pos, num, idx = parse_range_from_tokens(filtered)
    if idx is None:
        content_tokens = filtered
    else:
        content_tokens = filtered[idx + 1 :]
    content = " ".join(content_tokens).strip()
    return target_id, pos, num, content


def parse_reply_args(
    event: AiocqhttpMessageEvent,
) -> tuple[str | None, int, int, str]:
    """解析 `回评 [@用户] [说说序号] [评论序号] <内容>`"""

    tokens = event.message_str.strip().split()
    if not tokens:
        return None, 0, -1, ""

    tokens = tokens[1:]
    at_ids = get_ats(event)
    target_id = at_ids[0] if at_ids else None
    filtered = [t for t in tokens if not (t.startswith("@") and t[1:].isdigit())]

    # 说说序号
    pos, _, idx = parse_range_from_tokens(filtered)
    if idx is None:
        return target_id, 0, -1, ""

    # 评论序号
    if idx + 1 >= len(filtered):
        return target_id, pos, -1, ""
    comment_tok = filtered[idx + 1]
    comment_index = int(comment_tok) if comment_tok.lstrip("-").isdigit() else -1

    content = " ".join(filtered[idx + 2 :]).strip()
    return target_id, pos, comment_index, content
