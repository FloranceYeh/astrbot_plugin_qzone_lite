from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.config import PluginConfigLite
from .core.qzone import QzoneAPI, QzoneSession
from .core.sender import SenderLite
from .core.service import LitePostService
from .core.utils import get_ats, get_image_urls, parse_comment_args, parse_range, parse_reply_args


class QzoneLitePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfigLite(config, context)
        self.session = QzoneSession(self.cfg)
        self.qzone = QzoneAPI(self.session, self.cfg)
        self.service = LitePostService(self.qzone, self.session)
        self.sender = SenderLite()

    async def terminate(self):
        if self.qzone:
            await self.qzone.close()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def _init_client(self, event: AiocqhttpMessageEvent):
        if not self.cfg.client:
            self.cfg.client = event.bot
            logger.debug("QQ空间Lite所需的 CQHttp 客户端已初始化")

    async def _get_posts(
        self,
        event: AiocqhttpMessageEvent,
        *,
        target_id: str | None = None,
        with_detail: bool = False,
    ):
        pos, num = parse_range(event)
        at_ids = get_ats(event)
        if not target_id:
            target_id = at_ids[0] if at_ids else None

        try:
            return await self.service.query_feeds(
                target_id=target_id,
                pos=pos,
                num=num,
                with_detail=with_detail,
            )
        except Exception as e:
            await event.send(event.plain_result(str(e)))
            logger.error(e)
            event.stop_event()
            return []

    @filter.command("看说说", alias={"查看说说"})
    async def view_feed(self, event: AiocqhttpMessageEvent):
        posts = await self._get_posts(event, with_detail=True)
        for post in posts:
            await self.sender.send_post(event, post)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_feed(self, event: AiocqhttpMessageEvent):
        text = event.message_str.partition(" ")[2]
        images = await get_image_urls(event)
        try:
            post = await self.service.publish_post(text=text, images=images)
            await self.sender.send_post(event, post, message="已发布")
            event.stop_event()
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)

    @filter.command("评说说", alias={"评论说说", "读说说"})
    async def comment_feed(self, event: AiocqhttpMessageEvent):
        target_id, pos, num, content = parse_comment_args(event)
        if not content:
            yield event.plain_result("请在命令末尾提供评论内容，例如：评说说 0 路过~")
            return

        try:
            posts = await self.service.query_feeds(
                target_id=target_id,
                pos=pos,
                num=num,
                with_detail=False,
            )
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)
            return

        for post in posts:
            try:
                await self.service.comment_posts(post, content)
                await self.sender.send_post(event, post, message="已评论")
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.command("回评", alias={"回复评论"})
    async def reply_comment(self, event: AiocqhttpMessageEvent):
        target_id, pos, comment_index, content = parse_reply_args(event)
        if not content:
            yield event.plain_result(
                "请提供回复内容，例如：回评 0 -1 谢谢你的评论"
            )
            return

        try:
            posts = await self.service.query_feeds(
                target_id=target_id,
                pos=pos,
                num=1,
                with_detail=True,
            )
            if not posts:
                yield event.plain_result("查询结果为空")
                return
            post = posts[0]
            await self.service.reply_comment(post, comment_index, content)
            await self.sender.send_post(event, post, message="已回复评论")
        except Exception as e:
            await event.send(event.plain_result(str(e)))
            logger.error(e)
