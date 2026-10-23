# 更新日志

SpectreCore 的主要版本更新记录。历史版本的独立文件仍保存在 `changelogs/` 目录。

## v2.4.0

- **并发回复编排** - 新增默认关闭的 `reply_orchestration`，规则门控后直接请求指定 provider，一次 JSON 决策同时生成回复；支持并发上限和超时，失败按 ignore 处理。
- **账号级发送队列** - 新增默认关闭的队列，支持账号 token bucket、单群间隔、抖动、TTL、优先级、去重、容量限制和发送失败重试；关闭队列时立即发送。
- **异步历史存储** - 历史读写移至线程，按聊天加锁并原子替换文件；直接发送成功后手动保存机器人历史。
- **调用状态修复** - in-progress 延续到结果生命周期结束，增加超时兜底和请求归属检查，避免旧事件清除新请求状态。
- **兼容旧模式** - 默认沿用 legacy；model 模式不启用工具或流式输出，不修改人格，仅向明确支持图片的 provider 传原始图片。

## v2.3.1

- **提升多群并发** - 图像转述只处理实际纳入上下文的图片，历史、当前消息和合并转发中的图片改为并行预取，同一图片跨群去重
- **修复多群响应变慢** - 避免为未使用的历史图片逐张调用视觉模型，降低多群同时回复时的排队和延迟
- **新增 `image_caption_concurrency`** - 控制所有会话合计的图片转述并发数，默认 3，建议 3-5

## v2.3.0

- ✨ **合并转发解析** - 新增 `utils/forward_utils.py`，将 `Forward` / `Node` / `Nodes` 展开为带发送者、时间和图片编号的聊天记录；支持 OneBot `get_forward_msg` 拉取、嵌套转发与转发内图片
- ✨ **安全降级** - 拉取失败、超时、协议端不支持或超过层数限制时保持 `[合并转发消息]` 占位，不影响正常回复
- ✨ **新增转发配置** - `forward_processing.parse_forward`（默认关闭）、`max_depth`（默认 2）、`max_nodes`（默认 50）、`max_chars`（默认 2000）、`fetch_timeout`（默认 5 秒）
- 🔧 **上下文统一** - 转发内图片复用 `[图片#编号]`；当前消息和引用消息中的转发图片会随请求附带，历史转发中的图片暂不参与历史图片筛选，只显示为 `[图片]`

## v2.2.1

- ✨ **图片上下文关联** - 聊天记录、当前消息和引用消息中的图片会统一带上 `[图片#编号]` 标记，模型可以据此判断图片属于哪条消息、由谁发送，修复“用户回复/吐槽某张图片但模型看不懂”的问题
- ✨ **图片上下文配置** - 新增 `image_context_messages`（上下文消息数，默认 10）和 `image_context_max_images`（上下文图片上限，默认 5），独立控制向模型提供多少图片上下文，由模型自行判断哪些图片与当前对话相关
- 🐛 **修复图片错误关联** - 修复多张不相关图片被模型强行联系在一起的问题；当前消息本身及其引用消息中的图片始终会随请求提供，不受 `image_count` 限制
- 🔧 **元数据更新** - 按 AstrBot 要求补充 `short_desc`、`astrbot_version`，版本号更新为 `2.2.1`

## v2.2.0

- ✨ **模型自主引用** - 新增 `smart_quote` 配置（默认开启），AI 可以像真人一样自己决定是否引用、引用哪条消息。开启 AstrBot「回复时引用发送人消息」时，每条回复仍会引用，但改为引用 AI 真正回应的那条，修复引用错位的问题；未开启时 AI 可能会主动引用消息，不需要可关闭 `smart_quote`。需要 AstrBot v4.19.3 及以上 [#104](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/104)
- 🐛 **修复卡片消息解析** - 修复 AstrBot v4.10.0 起 QQ 小程序、分享卡片等消息传给 AI 时只显示为 [JSON消息]、丢失标题的问题 [#104](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/104)

## v2.1.13

- 🐛 **修复图片路径解析** - 统一使用 AstrBot 消息组件解析图片路径，修复 aiocqhttp / OneBot 图片消息在图像转述和多模态输入场景下可能无法正确读取的问题 [#98](https://github.com/23q3/astrbot_plugin_SpectreCore/pull/101)
- 🐛 **修复历史图片读取** - 修复持久化图片仍可能优先使用过期 URL 的问题，确保历史图片可从本地持久化文件读取 [#98](https://github.com/23q3/astrbot_plugin_SpectreCore/pull/101)
- 🐛 **修复历史图片计数** - 修复当前消息图片占用历史图片输入额度的问题，确保 `image_count` 只限制历史图片数量 [#98](https://github.com/23q3/astrbot_plugin_SpectreCore/pull/101)

## v2.1.12

- 🐛 **修复图片遗漏** - call_llm()遗漏当前消息图片导致模型无法读取用户上传的图片 [#96](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/96)

## v2.1.11

- 🐛 **修复机器人私聊消息存储路径错误** - 修复私聊中bot消息存入错误路径，导致用户看不到bot回复历史的问题 [#87](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/87)[#90](https://github.com/23q3/astrbot_plugin_SpectreCore/pull/90) @Hola-Gracias

## v2.1.10

- ✨ **新增群聊黑名单和全局回复开关** - 添加群聊黑名单功能，可禁用指定群的自动回复；新增全局回复开关，一键启用/禁用所有群的自动回复功能 [#61](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/61)[#83](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/83)
- 🔄 **使用AstrBot原生UMO人格机制** - 移除自定义人格注入逻辑，改用AstrBot原生的Unified Model Output人格机制，提高兼容性和稳定性 [#77](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/77)
- 🐛 **修复当前消息图片未被转述** - 修复当前消息中的图片未被包含在历史记录转述中的问题 [#84](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/84)[#85](https://github.com/23q3/astrbot_plugin_SpectreCore/pull/85) @lymangos

## v2.1.9

- 🐛 **修复GIF图片格式识别错误** - 修复图片URL优先级问题，优先使用file字段以保留准确格式信息，避免HTTP URL下载后MIME类型检测错误导致Gemini API返回400 [#81](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/81)

## v2.1.8

- ⚡ **优化LLM调用的prompt结构** - 将环境信息移至system_prompt，保持prompt干净以支持知识库检索；基于message_id精确排除当前消息避免历史记录重复 [#76](https://github.com/23q3/astrbot_plugin_SpectreCore/pull/76)
- 🐛 **修复QQ官方平台help命令** - 检测到QQ官方平台时不输出GitHub链接，避免消息被拦截 [#78](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/78)
- 🐛 **适配AstrBot新版本API** - 移除对已删除属性的依赖，迁移至新版人格管理接口，增强图片URL提取兼容性

## v2.1.7

- 🐛 **修复AstrBot兼容性问题** - 移除对不存在的 `Anonymous` 等消息组件类的依赖，使用类型字符串检查替代类实例检查，兼容 AstrBot 新版本
- ⚡ **优化私聊回复机制** - 将私聊回复概率固定为1，确保历史消息格式统一
- ⚡ **优化引用消息显示** - 改进 Reply 组件处理，提供更完整的发送者信息和内容
- ⚡ **优化@消息处理** - 重构 'at' 和 'atall' 组件处理逻辑，提高代码清晰度和功能性
- [#72](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/72) [#73](https://github.com/23q3/astrbot_plugin_SpectreCore/pull/73) @Hola-Gracias

## v2.1.6

- 🐛 **修复空消息异常** - 修复napcat发送私聊"正在输入"状态时，导致插件异常的问题 [#70](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/70)
- 🐛 **修复代码问题并改进错误处理** - 修复装饰器参数位置错误、历史记录加载失败崩溃问题，改进路径处理和日志记录

## v2.1.5

- ✨ **新增临时禁言功能** - 添加闭嘴/说话指令，支持临时禁用自动回复功能，默认5分钟，可自定义时长 [#63](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/63)
- 🔒 **添加权限控制** - 对管理指令添加了管理员权限限制

## v2.1.4

- ✨ **为LLM回复添加限制条件** - 在结尾提示词中添加对于回复的限制，避免LLM使用[At:id(昵称)]这样的格式 [#54](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/54)@Fossssss
- 🐛 **修复与其他插件的兼容性问题** - 移除AiocqhttpMessageEvent类型断言，使用安全检查和异常处理机制，解决其他插件构造输入时的报错问题 [#57](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/57)

## v2.1.3

- ✨ **新增图片持久化存储功能** - 添加图片本地存储和自动清理机制，解决聊天记录中图片链接过期问题。新增 `enable_image_persistence` 和 `image_retention_days` 配置选项 [#52](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/52)
- 🐛 **修复读空气功能干扰命令执行的问题** - 将读空气处理逻辑从on_llm_response移至on_decorating_result阶段，避免在大模型回复后立即停止事件传播导致命令逻辑被中断 [#33](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/33)

## v2.1.2

- 🐛 **修复Reply消息处理错误** - 修复在处理包含回复消息的历史记录时出现'Reply' object has no attribute 'sender_str'错误的问题 [#46](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/46)

## v2.1.1

- ✨ **新增黑名单关键词功能** - 添加黑名单关键词配置，可以设置不触发回复的关键词
- 🐛 **修复重置历史记录问题** - 修复重置聊天记录后提示消息被错误保存到历史记录的问题 [#41](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/41)

## v2.1.0

- 🔄 **数据存储格式优化** - 使用jsonpickle库替换pickle，提高数据可读性和跨平台兼容性
- 🐛 **修复Docker环境兼容性** - 采用JSON序列化格式，彻底解决Docker环境下消息历史存取问题 [#31](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/31)
- 🐛 **修复私聊时无法正确保存bot消息的问题** - 修复了在私聊场景下bot发送的消息无法被正确保存到历史记录的问题，确保私聊对话的完整性

## v2.0.1

- 🐛 **修复Docker部署问题** - 改进路径处理方式，修复在Docker环境下无法保存/读取消息历史的问题

## v2.0.0

- 🏗️ 完全重构 抛弃使用协议端API获取聊天记录的方式，改为基于Astrbot本身，支持了更多消息平台 [#21](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/21) [#4](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/4)
- 🔄 架构改进 采用高度模块化设计，每个功能封装在独立工具类中
- 📸 图片转述 支持图片转述功能 [#16](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/16)

## v1.0.4

- 🐛 修正处理大模型回复时的条件判断逻辑 [#15](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/15)

## v1.0.3

- 🐛 在处理大模型回复时增加了对角色的判断，避免调用函数工具时出错 [#15](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/15)
- 🐛 在提示词增加了bot的昵称和qq号，避免大模型不知道聊天记录中哪个是自己 [#14](https://github.com/23q3/astrbot_plugin_SpectreCore/issues/14)

## v1.0.2

- 🔒 添加了群组锁机制，防止并发调用大模型
- 🛠️ 优化了消息处理存储流程，极大提高了性能
- 🔍 添加了清除聊天记录的指令
- 🔍 添加了检测指令关键词不回复功能
- 📝 改进了代码结构

## v1.0.1

- 🔍 增加了读空气功能
- 🔍 增加了函数工具开关配置
- 🔄 更换了request_llm方法调用大模型，提高兼容性
- 🛠️ 优化部分代码

## v1.0.0

- 🎉 首次发布
- ✨ 实现基本的群聊互动功能
