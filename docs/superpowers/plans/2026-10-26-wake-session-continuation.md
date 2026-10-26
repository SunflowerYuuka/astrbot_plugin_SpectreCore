# Wake Session Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在自主回复实际发送成功后，按可配置的时间、消息数和空闲超时继续观察群聊/私聊，并让模型逐条判断是否继续回复。

**Architecture:** 新增 `WakeManager` 管理按账号/聊天隔离的唤醒状态和限制解析。唤醒开启时，首次概率/主动触发和唤醒期间的后续消息统一走一次 JSON 判断+生成请求；只有 OutboundQueue 实际发送成功后才激活或刷新唤醒状态。`ignore` 只跳过当前消息，不结束状态；状态由总时长、处理消息数或空闲超时结束。

**Tech Stack:** Python 3.10+、asyncio、AstrBot `event.is_admin()`、现有 `RuleGate`、`ReplyOrchestrator`、`OutboundQueue`、unittest。

**Spec:** `docs/superpowers/specs/2026-10-23-spectrecore-reply-orchestration-design.md`；本计划补充唤醒连续会话规则。

## Global Constraints

- `reply_orchestration.wake.enabled` 默认 `false`，保持当前 legacy/model 行为不变。
- `duration_seconds`、`max_messages`、`idle_timeout_seconds` 默认分别为 `300`、`5`、`120`；每项为 `0` 表示不限。
- 配置优先级：机器人管理员 `admin` > 群聊 `groups.<group_id>` / 私聊 `private` > 全局默认值；覆盖对象中未填写字段继承全局默认，显式 `0` 保留为不限。
- 机器人管理员使用 AstrBot `event.is_admin()` 判断，不使用群聊管理员身份；管理员配置默认三项均为 `0`。
- 私聊共用一套 `private` 配置，不按用户拆分；群聊可按群号覆盖。
- 首次概率/主动触发和唤醒期间的每条消息都只调用一次 JSON 判断+生成；返回 `ignore` 不产生发送项。
- 唤醒期间每条消息仍必须重新判断话题归属和是否适合插话；不得把唤醒理解为强制回复。
- 唤醒仅在实际发送成功后激活；队列拒绝、TTL 丢弃、发送异常、取消或历史保存失败不算发送成功。
- `/sc callllm`、普通手动测试和未触发自主回复的 legacy 请求不激活唤醒。
- 唤醒状态按 `platform_id:self_id:chat_type:chat_id` 隔离；不同群、私聊和账号互不影响。
- 不新增第三方依赖；WebUI 配置使用现有 `_conf_schema.json` 的嵌套 object 结构，保持默认折叠行为。
- 测试命令统一使用 `python -m unittest discover -s tests -t .`；提交仅用户明确授权时执行。

## File Structure

| File | Responsibility |
|---|---|
| `utils/wake_manager.py` | 唤醒限制解析、状态创建/查询/消费/结束和发送成功激活 |
| `utils/reply_orchestrator.py` | 复用 JSON 判断+生成，追加唤醒上下文提示，携带唤醒元数据入队 |
| `utils/outbound_queue.py` | 发送成功回调，保证只有真实 sender 成功后通知唤醒管理器 |
| `main.py` | 配置初始化、首次触发与唤醒消息路由、发送成功后的唤醒激活 |
| `utils/rule_gate.py` | 保留硬门控，提供 legacy 首次触发和唤醒期间的基础检查 |
| `_conf_schema.json` | 全局、私聊、管理员和群聊覆盖的嵌套唤醒配置 |
| `tests/test_wake_manager.py` | 限制优先级、0 不限、计数、超时、管理员和状态隔离 |
| `tests/test_reply_orchestrator.py` | JSON 唤醒提示和队列元数据 |
| `tests/test_outbound_queue.py` | 发送成功/失败/取消回调行为 |
| `tests/test_reply_orchestration_dispatch.py` | legacy/model 首次触发和唤醒消息路由 |
| `tests/test_wake_integration.py` | 首次成功发送激活，后续 ignore 继续观察，限制结束 |
| `README.md`, `CHANGELOG.md`, `changelogs/v2.6.0.md`, `metadata.yaml`, `main.py` | v2.6.0 配置说明和版本一致性 |

---

### Task 1: WakeManager 状态与限制解析

**Files:**
- Create: `utils/wake_manager.py`
- Test: `tests/test_wake_manager.py`

**Interfaces:**
- Produces `WakeLimits(duration_seconds: int, max_messages: int, idle_timeout_seconds: int)`。
- Produces `WakeManager.resolve_limits(event, config) -> WakeLimits`。
- Produces `WakeManager.is_active(event, now=None) -> bool`。
- Produces `WakeManager.claim_message(event, config, now=None) -> bool`，只在唤醒期间消费一条后续消息配额。
- Produces `WakeManager.activate(key, limits, now=None)` 和 `WakeManager.key_for_event(event)`。
- Produces `WakeManager.clear_expired(now=None)`、`reset()`。

- [x] **Step 1: 写失败测试**

```python
import time
import types
import unittest

from tests._orchestration_helpers import load_utils_module


class WakeManagerTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_utils_module("wake_manager")
        self.manager = self.mod.WakeManager
        self.manager.reset()
        self.event = types.SimpleNamespace(
            get_platform_name=lambda: "p",
            get_self_id=lambda: "bot",
            is_private_chat=lambda: False,
            get_group_id=lambda: "123",
            get_sender_id=lambda: "u",
            is_admin=lambda: False,
        )
        self.config = {"reply_orchestration": {"wake": {
            "enabled": True, "duration_seconds": 300,
            "max_messages": 2, "idle_timeout_seconds": 120,
            "private": {"duration_seconds": 10, "max_messages": 1, "idle_timeout_seconds": 5},
            "admin": {"duration_seconds": 0, "max_messages": 0, "idle_timeout_seconds": 0},
            "groups": {"123": {"duration_seconds": 600, "max_messages": 4}},
        }}}

    def test_group_override_inherits_missing_fields(self):
        limits = self.manager.resolve_limits(self.event, self.config)
        self.assertEqual(limits.duration_seconds, 600)
        self.assertEqual(limits.max_messages, 4)
        self.assertEqual(limits.idle_timeout_seconds, 120)

    def test_private_and_admin_configs_have_priority(self):
        self.event.is_private_chat = lambda: True
        limits = self.manager.resolve_limits(self.event, self.config)
        self.assertEqual((limits.duration_seconds, limits.max_messages, limits.idle_timeout_seconds), (10, 1, 5))
        self.event.is_private_chat = lambda: False
        self.event.is_admin = lambda: True
        limits = self.manager.resolve_limits(self.event, self.config)
        self.assertEqual((limits.duration_seconds, limits.max_messages, limits.idle_timeout_seconds), (0, 0, 0))

    def test_zero_limits_do_not_expire(self):
        limits = self.mod.WakeLimits(0, 0, 0)
        key = self.manager.key_for_event(self.event)
        self.manager.activate(key, limits, now=100)
        self.assertTrue(self.manager.is_active(self.event, now=10_000))
        self.assertTrue(self.manager.claim_message(self.event, self.config, now=10_000))

    def test_claim_counts_messages_and_expires(self):
        limits = self.mod.WakeLimits(100, 2, 30)
        key = self.manager.key_for_event(self.event)
        self.manager.activate(key, limits, now=100)
        self.assertTrue(self.manager.claim_message(self.event, self.config, now=110))
        self.assertTrue(self.manager.claim_message(self.event, self.config, now=120))
        self.assertFalse(self.manager.claim_message(self.event, self.config, now=121))
        self.manager.activate(key, limits, now=200)
        self.assertFalse(self.manager.is_active(self.event, now=231))
```

- [x] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_wake_manager -v`
Expected: FAIL，`wake_manager` 不存在。

- [x] **Step 3: 实现状态管理**

```python
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class WakeLimits:
    duration_seconds: int = 300
    max_messages: int = 5
    idle_timeout_seconds: int = 120


@dataclass
class WakeState:
    limits: WakeLimits
    started_at: float
    last_activity_at: float
    consumed_messages: int = 0


class WakeManager:
    _states: dict[str, WakeState] = {}

    @staticmethod
    def key_for_event(event) -> str:
        chat_type = "private" if event.is_private_chat() else "group"
        chat_id = event.get_sender_id() if event.is_private_chat() else event.get_group_id()
        return f"{event.get_platform_name()}:{event.get_self_id()}:{chat_type}:{chat_id}"

    @classmethod
    def _scope_config(cls, event, config):
        wake = (config.get("reply_orchestration", {}) or {}).get("wake", {}) or {}
        if getattr(event, "is_admin", lambda: False)():
            return wake.get("admin", {}) or {}
        if event.is_private_chat():
            return wake.get("private", {}) or {}
        return (wake.get("groups", {}) or {}).get(str(event.get_group_id()), {}) or {}

    @classmethod
    def resolve_limits(cls, event, config) -> WakeLimits:
        wake = (config.get("reply_orchestration", {}) or {}).get("wake", {}) or {}
        override = cls._scope_config(event, config)
        return WakeLimits(
            int(override.get("duration_seconds", wake.get("duration_seconds", 300))),
            int(override.get("max_messages", wake.get("max_messages", 5))),
            int(override.get("idle_timeout_seconds", wake.get("idle_timeout_seconds", 120))),
        )

    @classmethod
    def enabled(cls, config) -> bool:
        return bool(((config.get("reply_orchestration", {}) or {}).get("wake", {}) or {}).get("enabled", False))

    @classmethod
    def activate(cls, key, limits, now=None):
        now = time.time() if now is None else now
        cls._states[key] = WakeState(limits, now, now)

    @classmethod
    def is_active(cls, event, now=None) -> bool:
        now = time.time() if now is None else now
        state = cls._states.get(cls.key_for_event(event))
        if state is None:
            return False
        if state.limits.duration_seconds and now - state.started_at >= state.limits.duration_seconds:
            cls._states.pop(cls.key_for_event(event), None)
            return False
        if state.limits.idle_timeout_seconds and now - state.last_activity_at >= state.limits.idle_timeout_seconds:
            cls._states.pop(cls.key_for_event(event), None)
            return False
        if state.limits.max_messages and state.consumed_messages >= state.limits.max_messages:
            cls._states.pop(cls.key_for_event(event), None)
            return False
        return True

    @classmethod
    def claim_message(cls, event, config, now=None) -> bool:
        now = time.time() if now is None else now
        if not cls.enabled(config) or not cls.is_active(event, now):
            return False
        state = cls._states[cls.key_for_event(event)]
        state.consumed_messages += 1
        state.last_activity_at = now
        return True

    @classmethod
    def reset(cls):
        cls._states.clear()
```

- [x] **Step 4: 运行测试确认通过**

Run: `python -m unittest tests.test_wake_manager -v`
Expected: PASS。

---

### Task 2: JSON 唤醒判断提示与发送元数据

**Files:**
- Modify: `utils/decision_protocol.py`
- Modify: `utils/reply_orchestrator.py`
- Modify: `utils/outbound_queue.py`
- Test: `tests/test_reply_orchestrator.py`
- Test: `tests/test_outbound_queue.py`

**Interfaces:**
- Produces `build_wake_instruction() -> str`。
- Extends `OutboundItem` with `wake_key: str | None = None` and `wake_limits: WakeLimits | None = None`。
- `ReplyOrchestrator.handle(event, gate_result, wake_context=False)` adds wake instruction only for wake checks and carries limits metadata.
- `OutboundQueue` accepts optional `delivery_callback(item)` and awaits it only after sender returns true and history save succeeds.

- [x] **Step 1: 写失败测试**

```python
async def test_wake_instruction_requires_topic_awareness(self):
    protocol = load_utils_module("decision_protocol")
    text = protocol.build_wake_instruction()
    self.assertIn("不要因为刚才回复过就继续回复", text)
    self.assertIn("ignore", text)

async def test_successful_delivery_calls_wake_callback(self):
    callback = AsyncMock()
    queue = OutboundQueue(config, sender, history_saver, delivery_callback=callback)
    await queue.enqueue(item_with_wake_metadata)
    await saved_event.wait()
    callback.assert_awaited_once_with(item_with_wake_metadata)

async def test_failed_delivery_does_not_call_wake_callback(self):
    queue = OutboundQueue(config, failing_sender, history_saver, delivery_callback=AsyncMock())
    await queue.enqueue(item_with_wake_metadata)
    await asyncio.sleep(0.05)
    callback.assert_not_awaited()
```

- [x] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_reply_orchestrator tests.test_outbound_queue -v`
Expected: FAIL，缺少唤醒提示、队列回调参数和队列项元数据。

- [x] **Step 3: 实现一次请求判断和成功回调**

`build_wake_instruction()` 必须明确：唤醒只表示继续观察；每条新消息重新判断是否属于当前话题、是否被询问、是否会打扰群聊；ignore 只忽略当前消息；无关新话题、别人之间的对话、重复插话和明显厌烦信号应 ignore。

`ReplyOrchestrator.handle()` 在 `wake_context=True` 时把该提示追加到 JSON 决策协议；初次触发仍复用一次 JSON 判断+生成，不使用 legacy `<NO_RESPONSE>` 生成路径。`OutboundQueue` 只有 sender 成功、历史保存成功后才 await `delivery_callback`；失败、取消、TTL 丢弃不回调。

- [x] **Step 4: 运行测试确认通过**

Run: `python -m unittest tests.test_reply_orchestrator tests.test_outbound_queue -v`
Expected: PASS。

---

### Task 3: 配置与 WebUI 折叠

**Files:**
- Modify: `_conf_schema.json`
- Test: `tests/test_reply_orchestration_config.py`

**Interfaces:**
- Adds `reply_orchestration.wake.enabled` default `false`。
- Adds global `duration_seconds=300`、`max_messages=5`、`idle_timeout_seconds=120`。
- Adds `private` object with same three fields defaulting to global values。
- Adds `admin` object with all three fields default `0`。
- Adds `groups` object whose keys are group IDs and values contain optional override fields defaulting to absent。

- [x] **Step 1: 写失败测试**

```python
def test_wake_schema_defaults_and_scopes(self):
    items = schema["reply_orchestration"]["items"]
    wake = items["wake"]["items"]
    self.assertFalse(wake["enabled"]["default"])
    self.assertEqual(wake["duration_seconds"]["default"], 300)
    self.assertEqual(wake["max_messages"]["default"], 5)
    self.assertEqual(wake["idle_timeout_seconds"]["default"], 120)
    self.assertEqual(wake["admin"]["items"]["max_messages"]["default"], 0)
    self.assertEqual(wake["private"]["items"]["duration_seconds"]["default"], 300)
    self.assertEqual(wake["groups"]["type"], "object")
```

- [x] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_reply_orchestration_config -v`
Expected: FAIL，schema 尚未有 wake 配置。

- [x] **Step 3: 添加嵌套配置**

在 `reply_orchestration` 的 `items` 中添加 `wake` object，并把 `private`、`admin`、`groups` 放在该 object 内；所有字段使用现有 schema object/items 结构，使 WebUI 显示为折叠层级。`groups` 的 `items` 使用包含可选三项的 object 描述，未配置字段不写 default，便于继承全局。

- [x] **Step 4: 运行测试确认通过**

Run: `python -m unittest tests.test_reply_orchestration_config -v`
Expected: PASS。

---

### Task 4: main 路由与发送成功激活

**Files:**
- Modify: `main.py`
- Modify: `utils/reply_decision.py`
- Modify: `utils/__init__.py`
- Test: `tests/test_reply_orchestration_dispatch.py`
- Test: `tests/test_wake_integration.py`

**Interfaces:**
- `SpectreCore` owns one `WakeManager` state store and passes `self._on_outbound_delivered` to `OutboundQueue`。
- `_process_message()` checks active wake before normal probability/trigger logic；active wake consumes one message and routes to `ReplyOrchestrator.handle(event, gate_result, wake_context=True)`。
- On inactive wake with legacy mode and existing `ReplyDecision.should_reply()` true, route to the JSON orchestrator instead of legacy `process_and_reply()` when `wake.enabled=true`。
- On model mode retain existing RuleGate path, but pass `wake_context=WakeManager.is_active(event)`。
- `_on_outbound_delivered(item)` activates `item.wake_key` with `item.wake_limits` only after successful delivery；if already active, refresh state according to the configured session policy without resetting consumed count。

- [x] **Step 1: 写失败测试**

```python
async def test_legacy_trigger_uses_json_orchestrator_when_wake_enabled(self):
    config = {"reply_orchestration": {"enabled": False, "decision_mode": "legacy", "wake": {"enabled": True}}}
    patch_should_reply = patch.object(ReplyDecision, "should_reply", return_value=True)
    patch_handle = patch.object(ReplyOrchestrator, "handle", new=AsyncMock())
    with patch_should_reply, patch_handle:
        await collect(plugin._process_message(event))
    patch_handle.assert_awaited_once()

async def test_wake_ignore_keeps_state_and_next_message_is_checked(self):
    manager.activate(key, limits, now=100)
    decision = ignore_result
    await plugin._process_message(first_event)
    self.assertTrue(manager.is_active(first_event, now=110))
    await plugin._process_message(second_event)
    self.assertEqual(orchestrator.handle.await_count, 2)

async def test_only_successful_delivery_activates_wake(self):
    await orchestrator.handle(event, gate)
    self.assertFalse(manager.is_active(event))
    await queue_delivery_callback(item)
    self.assertTrue(manager.is_active(event))
```

- [x] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_reply_orchestration_dispatch tests.test_wake_integration -v`
Expected: FAIL，legacy 仍直接走旧流程，main 未连接 WakeManager 和 delivery callback。

- [x] **Step 3: 接入主流程**

在 `main.py` 初始化 `WakeManager` 使用的共享类状态，构造队列时传入 `_on_outbound_delivered`。`_process_message()` 的顺序固定为：空消息过滤 -> 用户历史保存 -> 检查唤醒状态；唤醒状态有效时 claim 并进入 JSON orchestrator；否则按当前 legacy/model 触发逻辑执行。唤醒 ignore 不调用清理，继续保持状态；过期、达到消息数或 idle timeout 时由 manager 移除。

首次 legacy 触发只有在原有 `ReplyDecision.should_reply()` 为 true 时才进入 JSON orchestrator；未触发时保持原行为。`/sc callllm` 不经过 `_process_message()` 的唤醒激活路径。

- [x] **Step 4: 运行测试确认通过**

Run: `python -m unittest tests.test_reply_orchestration_dispatch tests.test_wake_integration -v`
Expected: PASS。

---

### Task 5: 全量回归、文档和版本

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Create: `changelogs/v2.6.0.md`
- Modify: `metadata.yaml`
- Modify: `main.py`
- Modify: `tests/test_version_consistency.py`

- [x] **Step 1: 更新版本文档**

将版本升为 `2.6.0`。README 增加唤醒配置示例，明确 `0` 表示不限、群聊/私聊/admin 优先级、ignore 继续观察、只有实际发送成功才激活；CHANGELOG 和独立 changelog 说明一次 JSON 判断、空闲退出及不无脑续聊。

- [x] **Step 2: 更新版本测试**

把版本一致性断言改为 `2.6.0`，并要求 `changelogs/v2.6.0.md` 存在。

- [x] **Step 3: 运行完整验证**

Run: `python -m unittest discover -s tests -t .`
Expected: PASS。

Run: `python -m compileall -q main.py utils tests`
Expected: 无输出。

Run: `git diff --check`
Expected: 无空白错误。

- [x] **Step 4: 提交（仅用户授权时）**

```bash
git add _conf_schema.json main.py metadata.yaml README.md CHANGELOG.md changelogs/v2.6.0.md utils/wake_manager.py utils/reply_orchestrator.py utils/outbound_queue.py utils/reply_decision.py utils/rule_gate.py utils/decision_protocol.py utils/__init__.py tests/test_wake_manager.py tests/test_reply_orchestrator.py tests/test_outbound_queue.py tests/test_reply_orchestration_config.py tests/test_reply_orchestration_dispatch.py tests/test_wake_integration.py tests/test_version_consistency.py
git commit -m "feat: add configurable wake sessions"
```

## Self-Review

- **Spec coverage:** 覆盖一次 JSON 判断+生成、实际发送成功激活、ignore 继续观察、总时长/消息数/idle 三重限制、全局/群聊/private/admin 配置、管理员优先级、WebUI 嵌套折叠、legacy/model 路由、版本文档和回归测试。
- **Type consistency:** `WakeLimits`、`WakeState`、`WakeManager.resolve_limits()`、`OutboundItem.wake_key/wake_limits`、`delivery_callback(item)` 在任务间一致。
- **State safety:** 状态按账号和聊天隔离；队列失败、取消、TTL 丢弃不会激活；admin 默认值为 0；group 未填写字段继承全局，显式 0 不继承。
- **No placeholders:** 本计划不依赖 TODO/TBD；每个实现步骤都给出接口、测试行为和验证命令。
