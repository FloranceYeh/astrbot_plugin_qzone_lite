from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.provider.provider import Provider

from .core.config import PluginConfigLite
from .core.qzone import QzoneAPI, QzoneSession
from .core.sender import SenderLite
from .core.service import LitePostService
from .core.utils import (
    get_ats,
    get_image_urls,
    parse_comment_args,
    parse_range,
    parse_reply_args,
)


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

    async def _analyze_post_images(self, post) -> None:
        if not self.cfg.analyze_images_on_view_feed or not post.images:
            return
        if post.extra_text:
            return

        provider = (
            self.context.get_provider_by_id(self.cfg.vision_provider_id)
            or self.context.get_using_provider()
        )
        if not isinstance(provider, Provider):
            post.extra_text = "图片分析失败：未配置可用的视觉模型提供商"
            return

        prompt = (
            f"说说发布者：{post.name}({post.uin})\n"
            f"说说正文：{post.text or '(无文字)'}\n"
            f"转发内容：{post.rt_con or '(无转发内容)'}\n"
            "请只根据图片和以上上下文返回图片内容分析。"
        )
        try:
            response = await provider.text_chat(
                system_prompt=self.cfg.vision_prompt,
                prompt=prompt,
                image_urls=post.images,
            )
            post.extra_text = (response.completion_text or "").strip()
        except Exception as e:
            logger.error(e)
            post.extra_text = f"图片分析失败：{e}"

    # =========================
    # Commands
    # =========================

    @filter.command("看说说", alias={"查看说说"})
    async def view_feed(self, event: AiocqhttpMessageEvent):
        posts = await self._get_posts(event, with_detail=True)
        for post in posts:
            await self._analyze_post_images(post)
            await self.sender.send_post(event, post)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_feed(self, event: AiocqhttpMessageEvent):
        text = event.message_str.partition(" ")[2]
        images = await get_image_urls(event)
        try:
            post = await self.service.publish_post(text=text, images=images)
            if self.cfg.send_feedback:
                await self.sender.send_post(event, post, message="已发布")
            event.stop_event()
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删说说", alias={"删除说说"})
    async def delete_feed(self, event: AiocqhttpMessageEvent):
        posts = await self._get_posts(event, target_id=event.get_self_id(), with_detail=False)
        deleted_count = 0
        for post in posts:
            try:
                await self.service.delete_post(post)
                deleted_count += 1
                if self.cfg.send_feedback:
                    await self.sender.send_post(event, post, message="已删除说说")
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)
        if not self.cfg.send_feedback:
            await event.send(event.plain_result(f"已删除 {deleted_count} 条说说"))

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
                if self.cfg.send_feedback:
                    await self.sender.send_post(event, post, message="已评论")
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.command("赞说说", alias={"点赞说说"})
    async def like_feed(self, event: AiocqhttpMessageEvent):
        posts = await self._get_posts(event, with_detail=False)
        for post in posts:
            try:
                await self.service.like_post(post)
                if self.cfg.send_feedback:
                    await self.sender.send_post(event, post, message="已点赞")
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.command("回评", alias={"回复评论"})
    async def reply_comment(self, event: AiocqhttpMessageEvent):
        target_id, pos, comment_index, content = parse_reply_args(event)
        if not content:
            yield event.plain_result("请提供回复内容，例如：回评 0 -1 谢谢你的评论")
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
            if self.cfg.send_feedback:
                await self.sender.send_post(event, post, message="已回复评论")
        except Exception as e:
            await event.send(event.plain_result(str(e)))
            logger.error(e)

    # =========================
    # LLM Tools
    # =========================

    @staticmethod
    def _format_post_for_llm(post) -> str:
        return post.to_str()

    @filter.llm_tool()
    async def llm_view_feed(
        self,
        event: AiocqhttpMessageEvent,
        user_id: str | None = None,
        pos: int = 0,
    ) -> str:
        """查看某位用户的说说。

        Args:
            user_id(string): 目标 QQ 号，默认当前会话发送者
            pos(number): 说说序号（0 表示最新）
        """
        try:
            target = user_id or event.get_sender_id()
            posts = await self.service.query_feeds(
                target_id=target,
                pos=pos,
                num=1,
                with_detail=True,
            )
            if not posts:
                return "查询结果为空"
            post = posts[0]
            await self._analyze_post_images(post)
            if self.cfg.send_feedback:
                await self.sender.send_post(event, post)
            return self._format_post_for_llm(post)
        except Exception as e:
            logger.error(e)
            return str(e)

    @filter.llm_tool()
    async def llm_publish_feed(
        self,
        event: AiocqhttpMessageEvent,
        text: str = "",
        get_image: bool = True,
    ) -> str:
        """发布一条说说。

        Args:
            text(string): 说说正文
            get_image(boolean): 是否附带当前对话图片
        """
        try:
            images = await get_image_urls(event) if get_image else []
            post = await self.service.publish_post(text=text, images=images)
            if self.cfg.send_feedback:
                await self.sender.send_post(event, post, message="已发布")
            return "已发布说说\n" + self._format_post_for_llm(post)
        except Exception as e:
            logger.error(e)
            return str(e)

    @filter.llm_tool()
    async def llm_comment_feed(
        self,
        event: AiocqhttpMessageEvent,
        user_id: str | None = None,
        pos: int = 0,
        content: str = "",
    ) -> str:
        """评论一条说说（必须提供 content）。

        Args:
            user_id(string): 目标 QQ 号，默认当前会话发送者
            pos(number): 说说序号（0 表示最新）
            content(string): 评论内容（必填）
        """
        try:
            if not content or not content.strip():
                return "评论失败：content 不能为空"

            target = user_id or event.get_sender_id()
            posts = await self.service.query_feeds(
                target_id=target,
                pos=pos,
                num=1,
                with_detail=False,
            )
            if not posts:
                return "查询结果为空"
            post = posts[0]
            await self.service.comment_posts(post, content)
            if self.cfg.send_feedback:
                await self.sender.send_post(event, post, message="已评论")
            return "已评论\n" + self._format_post_for_llm(post)
        except Exception as e:
            logger.error(e)
            return str(e)

    @filter.llm_tool()
    async def llm_reply_comment(
        self,
        event: AiocqhttpMessageEvent,
        user_id: str | None = None,
        pos: int = 0,
        reply_index: int = -1,
        content: str = "",
    ) -> str:
        """回复某条说说下的评论（必须提供 content）。

        Args:
            user_id(string): 目标 QQ 号，默认当前会话发送者
            pos(number): 说说序号（0 表示最新）
            reply_index(number): 要回复的评论序号（基于说说详情的全部评论列表）
            content(string): 回复内容（必填）
        """
        try:
            if not content or not content.strip():
                return "回复失败：content 不能为空"

            target = user_id or event.get_sender_id()
            posts = await self.service.query_feeds(
                target_id=target,
                pos=pos,
                num=1,
                with_detail=True,
            )
            if not posts:
                return "查询结果为空"
            post = posts[0]
            await self.service.reply_comment(post, reply_index, content)
            if self.cfg.send_feedback:
                await self.sender.send_post(event, post, message="已回复评论")
            return "已回复评论\n" + self._format_post_for_llm(post)
        except Exception as e:
            logger.error(e)
            return str(e)

    @filter.llm_tool()
    async def llm_like_feed(
        self,
        event: AiocqhttpMessageEvent,
        user_id: str | None = None,
        pos: int = 0,
    ) -> str:
        """点赞某位用户的说说。

        Args:
            user_id(string): 目标 QQ 号，默认当前会话发送者
            pos(number): 说说序号（0 表示最新）
        """
        try:
            target = user_id or event.get_sender_id()
            posts = await self.service.query_feeds(
                target_id=target,
                pos=pos,
                num=1,
                with_detail=False,
            )
            if not posts:
                return "查询结果为空"
            post = posts[0]
            await self.service.like_post(post)
            if self.cfg.send_feedback:
                await self.sender.send_post(event, post, message="已点赞")
            return "已点赞\n" + self._format_post_for_llm(post)
        except Exception as e:
            logger.error(e)
            return str(e)
