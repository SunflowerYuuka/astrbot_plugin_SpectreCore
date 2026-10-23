# 🌟 SpectreCore (影芯) - 智能群聊互动插件

<div align="center">

![SpectreCore](https://avatars.githubusercontent.com/u/129108081?s=48&v=4)

[![version](https://img.shields.io/badge/version-v2.5.0-blue.svg?style=flat-square)](https://github.com/SunflowerYuuka/astrbot_plugin_SpectreCore)
[![license](https://img.shields.io/badge/license-AGPL--3.0-green.svg?style=flat-square)](LICENSE)
[![author](https://img.shields.io/badge/author-23q3-orange.svg?style=flat-square)](https://github.com/23q3)

*让 AI 成为群聊中最受欢迎的成员*

</div>

<p align="center">
  <a href="#-简介">简介</a> •
  <a href="#-特性">特性</a> •
  <a href="#-快速开始">快速开始</a> •
  <a href="#-文档">文档</a> •
  <a href="#-最新版本">最新版本</a> •
  <a href="#-注意事项">注意事项</a>
</p>

---

## 📝 简介

<table>
<tr>
<td>

SpectreCore (影芯) 是一个为 AstrBot 设计的高级群聊互动插件，它能让大语言模型更好地参与到群聊对话中，带来生动和沉浸式的群聊体验。通过智能的消息处理和上下文理解，让 AI 像真实群友一样自然地参与讨论。

> 💡 **灵感来源**：本项目的灵感来自 [【当你发现聊了99+的群友是DeepSeek.......】](https://www.bilibili.com/video/BV1amAneGE3P)

</td>
</tr>
</table>

## ✨ 特性

- 📸 **支持图片理解** - 支持图片转述功能，也可以直接输入图片
- 🧩 **图片上下文关联** - 聊天记录和当前消息中的图片会带 `[图片#编号]`，模型能判断图片属于哪条消息、由谁发送；用户引用图片回复时也能看到原图，避免把无关图片强行关联
- 📨 **合并转发解析** - 可开启将 QQ 合并转发展开为按发送者排列的聊天记录，支持嵌套转发和转发内图片；获取失败时自动回退为占位符
- 💾 **图片持久化存储** - 自动保存聊天中的图片到本地，解决图片链接过期问题，支持自动清理过期图片
- 🔍 **支持读空气** - AI 可智能判断何时应该回复，何时保持沉默
- 💬 **模型自主引用** - AI 像真人一样自己决定是否引用、引用哪条消息，开启 AstrBot 引用回复时也不再引用错位
- 🤐 **临时禁言功能** - 支持通过指令临时禁用自动回复，可自定义时长
- 📨 **高级消息格式处理** - 支持让大模型理解At、引用等消息
- 🌐 **多平台兼容** - 基于Astrbot，支持所有Astrbot支持的消息平台
- 🏗️ **模块化设计** - 采用高度模块化的架构，每个功能都被封装在独立的工具类中，便于维护和扩展

## 🚀 快速开始

### 安装

1. 首先确保已部署 [AstrBot](https://github.com/Soulter/AstrBot)
2. 在插件市场中搜索 SpectreCore 点击安装
   或点击右下角加号，输入本插件仓库链接安装：

   ```
   https://github.com/SunflowerYuuka/astrbot_plugin_SpectreCore
   ```

3. 打开插件的配置界面，配置好相关选项，即可开始使用

### 自主回复附加提示词（默认关闭）

在插件配置的「自主回复附加提示词」中启用开关，并在多行文本框填写自定义内容。适用于 legacy 的概率／关键词回复、model 编排模式和 `/sc callllm` 手动测试；普通 AstrBot 对话不会由本功能追加提示词。

```json
{
  "autonomous_reply_prompt": {
    "enabled": true,
    "text": "群聊中的消息默认是在群友之间交流，不要默认是在对你说。先辨认发送者、图片发布者和引用对象，不要把自己代入其他人的身份。没有自然且具体的补充时保持沉默。避免主动提出整理材料、做笔录等助手式服务。"
  }
}
```

内容仅拼接到本次请求的 `system_prompt`，不修改人格或共享配置，不作为消息写入插件历史；请求结束后自然失效，无需删除。关闭开关或内容为空白时不追加。插件仍会追加读空气、JSON 输出协议和引用规则；自定义内容应描述参与方式和表达风格，避免要求与这些规则冲突的输出格式。提示词能辅助判断，不能保证纠正输入中错误的发送者或引用关系。

### 并发回复编排（默认关闭）

在插件配置中开启 `reply_orchestration.enabled` 并将 `decision_mode` 设为 `model`。插件先检查群启用、禁言、关键词、@、冷却等规则，再通过一次模型请求同时判断是否回复并生成内容。`provider_id` 填 AstrBot 已配置的文本模型提供商 ID；留空时使用当前会话的提供商。群白名单、黑名单和私聊开关仍生效。

```json
{
  "reply_orchestration": {
    "enabled": true,
    "decision_mode": "model",
    "provider_id": "your-fast-cloud-provider-id",
    "max_parallel": 12,
    "timeout_ms": 2000,
    "outbound_queue": {
      "enabled": true,
      "global_interval_ms": 800,
      "per_group_interval_ms": 6000,
      "jitter_ms": 300,
      "max_burst": 3,
      "ttl_ms": 20000
    }
  }
}
```

生成请求最多并发 12 个，默认 2 秒超时（包含等待并发名额和构建请求）；超时、模型不可用或 JSON 无效时忽略该条消息。实际速度取决于云端模型、图片转述和网络，2 秒是生成目标，发送时间另外受队列控制。

发送队列也默认关闭；开启后按平台账号共享 token bucket，并限制单群发送间隔。默认每群最多待发 2 条，总计 200 条，过期项丢弃，发送失败最多重试 2 次。一次回复中的多条文本合并为一条消息链发送。队列关闭时生成完成后立即发送；两种模式都会在发送成功后保存机器人历史。重载或退出时未发送项丢弃。

可用 `max_context_messages`（默认 20）、`max_reply_messages`（3）、`min_confidence`（0.55）、`cooldown_ms`（3000）和 `max_replies_per_minute`（6）控制上下文与发言频率。`max_in_progress_ms` 默认 15000，须大于 `timeout_ms`；`fallback_on_error` 当前仅支持 `ignore`。只有提供商明确声明图片能力时才传原始图片，否则保留图片转述文本。

`model` 模式使用临时 JSON 提示词，不修改人格；直接请求模型，不启用工具或流式输出。关闭编排或选择 `legacy` 可继续使用原有 AstrBot LLM 链路和 `<NO_RESPONSE>` 读空气机制。DEBUG 日志记录生成耗时、门控原因和队列等待，便于调参。

`model` 模式也遵循 AstrBot 的「回复时引用发送人消息」设置：模型没有选定引用时，插件会在入队前补上触发消息的引用；已有自主引用时保留该引用，不重复添加。这个兜底不依赖 `smart_quote` 开关，队列开启和关闭时均生效；群聊同时开启「回复时 @ 发送人」时也会补上 @。

使用 `legacy` + `read_air=true` 时，本插件会对本次请求关闭流式发送，等待完整生成后判断 `<NO_RESPONSE>`，再进入 AstrBot 的引用装饰和发送流程。即使 AstrBot 全局开启流式也会生效；全局设置保持不变，关闭读空气后继续沿用原来的流式选择。

## 📖 文档

- [指令说明](./docs/commands.md) - 详细的指令用法和参数说明
- [使用技巧](./docs/tips.md) - 提高插件使用效果的技巧和建议
- [项目结构](./docs/structure.md) - 项目代码结构和核心文件说明
- [更新日志](./CHANGELOG.md) - 项目版本历史和功能变化

## 📋 最新版本

### v2.5.0 (2026-10-23)

- 新增默认关闭的自主回复附加提示词，支持在配置中填写多行群聊参与规则。
- 同时覆盖 legacy 概率／关键词回复、model 编排与手动测试；仅对本次请求生效，不修改人格或插件历史。
- 保留读空气、JSON 决策和引用提示，并验证并发与连续请求不会重复追加或残留。

### v2.4.1 (2026-10-23)

- **修复 legacy 读空气与流式冲突** - 开启读空气时，本插件的请求会完整生成后再发送，使 `<NO_RESPONSE>` 判断和 AstrBot 引用装饰生效；不修改全局流式设置。
- **修复 model 默认引用** - 遵循 AstrBot「回复时引用发送人消息」设置，无自主引用时补上触发消息的引用，已有引用不重复添加；群聊同步兼容 @ 发送人设置。

### v2.4.0 (2026-10-23)

- 新增默认关闭的并发回复编排，一次模型请求完成 JSON 决策和回复生成，支持独立选择 provider。
- 新增账号级发送队列，支持间隔、抖动、TTL、优先级、去重和失败重试。
- 历史读写改为异步线程与每聊天锁；修复 in-progress 生命周期及旧事件误清新请求的问题。

### v2.3.0 (2026-10-23)

- ✨ **合并转发解析** - 将 `Forward` / `Node` / `Nodes` 展开为带发送者和时间的聊天记录，支持 OneBot `get_forward_msg` 拉取、嵌套转发和转发内图片；失败时保持 `[合并转发消息]` 占位，不影响正常回复
- ✨ **新增转发配置** - `forward_processing.parse_forward`（默认关闭）、`max_depth`（默认 2）、`max_nodes`（默认 50）、`max_chars`（默认 2000）、`fetch_timeout`（默认 5 秒），用于控制 token 消耗和请求耗时
- 🔧 **上下文统一** - 转发里的图片会复用 `[图片#编号]`，当前消息和引用消息中的转发图片会随请求附带；历史转发中的图片暂不参与历史图片筛选，只显示为 `[图片]`

### v2.2.1 (2026-10-23)

- ✨ **图片上下文关联** - 聊天记录、当前消息和引用消息中的图片会统一带上 `[图片#编号]`，模型可以判断图片属于哪条消息、由谁发送；用户回复/吐槽某张图片时也能看到对应原图 [#104](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/104)
- ✨ **新增图片上下文配置** - `image_context_messages`（默认 10，向前查看多少条消息）和 `image_context_max_images`（默认 5，最多附带多少张上下文图片），由模型自行判断哪些图片与当前对话相关
- 🐛 **修复图片错误关联** - 历史图片不再无归属地混在一起，避免模型把两张不相关的图片强行联系在一起；当前消息及其引用消息中的图片始终提供，不受 `image_count` 限制

### v2.2.0 (2026-09-24)

- ✨ **模型自主引用** - 新增 `smart_quote` 配置（默认开启），AI 可以像真人一样自己决定是否引用、引用哪条消息。开启 AstrBot「回复时引用发送人消息」时，每条回复仍会引用，但改为引用 AI 真正回应的那条，修复引用错位的问题；未开启时 AI 可能会主动引用消息，不需要可关闭 `smart_quote`。需要 AstrBot v4.19.3 及以上 [#104](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/104)
- 🐛 **修复卡片消息解析** - 修复 AstrBot v4.10.0 起 QQ 小程序、分享卡片等消息传给 AI 时只显示为 [JSON消息]、丢失标题的问题 [#104](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/104)

## ⚠️ 注意事项

- 本插件和 AstrBot 自带的主动回复功能之间没有任何联系，在使用本插件时请关闭 AstrBot 的主动回复功能，以免重复回复
- 为避免不必要的响应，建议开启读空气功能并为 AI 提示明确的回复条件

### ⚠️ 更新到2.0.0版本的用户注意

- 删除Astrbot目录下的 data\config\astrbot_plugin_SpectreCore_config.json 否则会出现重复配置 且排版混乱(Astrbot特性导致)
- 这次更新是一次完全重构，抛弃了使用协议端api获取聊天记录的方式，而是基于Astrbot本身。

- 这导致了 对于aiocqhttp平台 插件不再支持以下功能:
  - 解析猜拳骰子结果
  - 获取QQ表情描述
  - 完全溯源的引用消息
  - 获取被@人的用户名

- 合并转发解析已在 v2.3.0 通过协议端 `get_forward_msg` 恢复；如果你的协议端不支持该接口，转发内容会显示为 `[合并转发消息]`。
- 如果你有相关需求，可以下载之前的版本，但请注意旧版本已不再维护。

## 🔗 相关项目

<div align="center">
<table>
<tr>
<td align="center">
<a href="https://github.com/SengokuCola/MaiMBot">
<img src="https://avatars.githubusercontent.com/u/25811392?s=48&v=4" width="80" alt="MaiMBot"><br>
<sub><b>MaiMBot (麦麦)</b></sub>
</a><br>
<sub>一款专注于群组聊天的赛博网友QQ机器人</sub>
</td>
</tr>
</table>
</div>

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来帮助改进这个项目！

[![contributors](https://contrib.rocks/image?repo=23q3/astrbot_plugin_SpectreCore)](https://github.com/23q3/astrbot_plugin_SpectreCore/graphs/contributors)

## 📄 许可证

本项目采用 GNU Affero General Public License v3.0 (AGPL-3.0) 许可证。

详细信息请查看 [LICENSE](LICENSE) 文件。

---

<div align="center">

**[SpectreCore (影芯)](https://github.com/SunflowerYuuka/astrbot_plugin_SpectreCore)** | Powered by [AstrBot](https://github.com/AstrBotDevs/AstrBot)

<sub>Made with ❤️ by [23q3](https://github.com/23q3)</sub>

</div>
