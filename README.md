# astrbot_plugin_qzone_lite

QQ 空间轻量插件，基于上游项目裁剪而来。  
项目来源：<https://github.com/Zhalslar/astrbot_plugin_qzone>

当前版本保留以下能力：

- 看说说（查看说说）
- 发说说（管理员）
- 评说说（需显式提供评论内容）
- 回评（需显式提供回复内容）

与上游相比，Lite 版本重点做了裁剪：去掉 DB 依赖、去掉 pillowmd 渲染链路，并将评论/回复能力收敛为“显式传参”模式，便于在轻量部署场景使用。

## 命令

- `看说说 [@用户] [序号/范围]`
- `发说说 <文本> [图片]`（管理员）
- `评说说 [@用户] [序号/范围] <评论内容>`
- `回评 [@用户] [说说序号] [评论序号] <回复内容>`

说明：
- 序号默认从 0/1 都可（两者都会指向最新一条）。
- `回评` 的“评论序号”基于当前说说详情的“排除自己评论后的列表”，与完整版一致。

## LLM Tools

本插件已提供 LLM Tool 接口（`@filter.llm_tool()`）：

- `llm_view_feed(user_id: str | None = None, pos: int = 0)`
  - 查看目标用户指定序号的说说（默认当前会话发送者，`0` 为最新）。
- `llm_publish_feed(text: str = "", get_image: bool = True)`
  - 发布说说，可选是否附带当前对话图片。
- `llm_comment_feed(user_id: str | None = None, pos: int = 0, content: str = "")`
  - 评论指定说说，`content` 必填。
- `llm_reply_comment(user_id: str | None = None, pos: int = 0, reply_index: int = -1, content: str = "")`
  - 回复指定评论，`content` 必填，`reply_index` 支持负数索引。

说明：
- 若 `send_feedback=true`，LLM Tool 执行时也会在会话中发送对应反馈消息。
- 以上工具的行为与同名命令保持一致，均依赖 QQ 空间登录态（Cookies / CQHTTP 会话）。
