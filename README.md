# astrbot_plugin_qzone_lite

QQ 空间轻量插件，基于上游项目裁剪而来。
项目来源：<https://github.com/Zhalslar/astrbot_plugin_qzone>

当前版本保留以下能力：

- 看说说（查看说说）
- 发说说（管理员）
- 删说说（管理员，仅删除自己说说）
- 评说说（需显式提供评论内容）
- 回评（需显式提供回复内容）
- 赞说说（点赞功能已修复）

与上游相比，Lite 版本重点做了裁剪：去掉 DB 依赖、去掉 pillowmd 渲染链路；同时添加“评论/回复内容可自定义（显式传参）”能力，并修复了点赞功能，便于在轻量部署场景使用。

## 命令

- `看说说 [@用户] [序号/范围]`
- `发说说 <文本> [图片]`（管理员）
- `删说说 [序号/范围]`（管理员，仅删除自己的说说）
- `重置QQCookies`（管理员，清空已保存的 QQ Cookies）
- `评说说 [@用户] [序号/范围] <评论内容>`
- `回评 [@用户] [说说序号] [评论序号] <回复内容>`
- `赞说说 [@用户] [序号/范围]`

说明：
- 序号默认从 0/1 都可（两者都会指向最新一条）。
- `回评` 的“评论序号”基于当前说说详情中的“全部评论列表”（包含自己的评论），与 `看说说` 展示序号一致。

## LLM Tools

- `llm_view_feed(user_id: str | None = None, pos: int = 0)`
  - 查看目标用户指定序号的说说（默认当前会话发送者，`0` 为最新）。
- `llm_publish_feed(text: str = "", get_image: bool = True)`
  - 发布说说，可选是否附带当前对话图片。
- `llm_delete_feed(user_id: str | None = None, pos: int = 0)`
  - 删除指定说说，仅能删除自己的说说。
- `llm_comment_feed(user_id: str | None = None, pos: int = 0, content: str = "")`
  - 评论指定说说，`content` 必填。
- `llm_reply_comment(user_id: str | None = None, pos: int = 0, reply_index: int = -1, content: str = "")`
  - 回复指定评论，`content` 必填，`reply_index` 支持负数索引。
- `llm_like_feed(user_id: str | None = None, pos: int = 0)`
  - 点赞指定说说。

说明：
- 若 `send_feedback=true`，LLM Tool 执行时也会在会话中发送对应反馈消息。
- 以上工具的行为与同名命令保持一致，均依赖 QQ 空间登录态（Cookies / CQHTTP 会话）。

配置位置：插件配置项 `send_feedback`（默认值 `true`，可在 AstrBot 插件配置面板中修改）。

## 许可证

本项目基于 [GNU General Public License v3.0](LICENSE) 授权。
