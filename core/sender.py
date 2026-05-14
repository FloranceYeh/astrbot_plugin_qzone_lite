from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .model import Post


class SenderLite:
    async def send_post(self, event: AstrMessageEvent, post: Post, *, message: str = ""):
        chain = []
        if message:
            chain.append(Plain(message))
        chain.append(Plain(post.to_str()))
        await event.send(event.chain_result(chain))
