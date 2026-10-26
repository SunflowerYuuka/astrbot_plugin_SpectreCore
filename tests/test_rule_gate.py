import types
import time
import unittest
from unittest.mock import AsyncMock, patch

from tests._orchestration_helpers import load_utils_module


class FakeEvent:
    def __init__(self, text="hello", group="123", private=False, components=None):
        self._text = text
        self._group = group
        self._private = private
        self._extra = {}
        self.message_obj = types.SimpleNamespace(message=components or [], message_id="m1")
        self.unified_msg_origin = f"p:{'private' if private else 'group'}:{group}"

    def get_platform_name(self): return "p"
    def is_private_chat(self): return self._private
    def get_group_id(self): return None if self._private else self._group
    def get_sender_id(self): return "u1"
    def get_self_id(self): return "bot"
    def get_message_outline(self): return self._text
    def set_extra(self, key, value): self._extra[key] = value
    def get_extra(self, key, default=None): return self._extra.get(key, default)


def config(**overrides):
    base = {
        "enabled_private": True,
        "enable_all_groups": True,
        "blocked_groups": [],
        "group_msg_history": 10,
        "model_frequency": {
            "keywords": ["触发"],
            "blacklist_keywords": ["拉黑"],
            "probability": {"probability": 0.0},
        },
        "reply_orchestration": {
            "cooldown_ms": 3000,
            "max_replies_per_minute": 6,
            "max_context_messages": 20,
            "outbound_queue": {"max_queue_size": 200},
        },
    }
    base.update(overrides)
    return base


class RuleGateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_utils_module("rule_gate")
        self.history = load_utils_module("history_storage").HistoryStorage
        self.history.config = config()
        self.mod.RuleGate.reset_state()

    def test_wake_check_accepts_message_without_normal_trigger(self):
        result = self.mod.RuleGate.check_wake_message(FakeEvent("ordinary text"), config())
        self.assertEqual((result.action, result.reason, result.trigger), ("candidate", "wake", "wake"))

    def test_wake_check_rejects_hard_gate_failures_without_a_trigger(self):
        gate = self.mod.RuleGate
        cases = [
            (FakeEvent("!help"), config(model_frequency={"ignored_prefixes": ["!"]}), "command_prefix"),
            (FakeEvent("拉黑内容"), config(model_frequency={"blacklist_keywords": ["拉黑"]}), "blacklist_keyword"),
        ]
        for event, cfg, reason in cases:
            with self.subTest(reason=reason):
                result = gate.check_wake_message(event, cfg)
                self.assertEqual((result.action, result.reason), ("skip", reason))

        self.history.config = config(enable_all_groups=False)
        disabled = gate.check_wake_message(FakeEvent("ordinary text"), self.history.config)
        self.assertEqual(disabled.reason, "chat_disabled")
        self.history.config = config()

        muted = config(_temp_mute={"until": time.time() + 60})
        result = gate.check_wake_message(FakeEvent("ordinary text"), muted)
        self.assertEqual(result.reason, "muted")

        self_id_event = FakeEvent("ordinary text")
        self_id_event.get_sender_id = lambda: "bot"
        result = gate.check_wake_message(self_id_event, config())
        self.assertEqual(result.reason, "self")

        duplicate = FakeEvent("ordinary text")
        duplicate.message_obj.message_id = "wake-duplicate"
        self.assertEqual(gate.check_wake_message(duplicate, config()).action, "candidate")
        self.assertEqual(gate.check_wake_message(duplicate, config()).reason, "duplicate")

        limited = FakeEvent("ordinary text", group="limited")
        gate.record_reply(limited)
        result = gate.check_wake_message(limited, config())
        self.assertEqual(result.reason, "cooldown")

    def test_wake_check_respects_queue_capacity(self):
        result = self.mod.RuleGate.check_wake_message(
            FakeEvent("ordinary text"), config(), queue_depth=200
        )
        self.assertEqual((result.action, result.reason), ("skip", "queue_full"))

    async def test_keyword_is_candidate(self):
        result = await self.mod.RuleGate.evaluate(FakeEvent("这里触发一下"), config())
        self.assertEqual(result.action, "candidate")
        self.assertEqual(result.trigger, "keyword")

    async def test_blocked_group_is_skipped(self):
        self.history.config = config(blocked_groups=["123"])
        result = await self.mod.RuleGate.evaluate(FakeEvent("hello"), self.history.config)
        self.assertEqual(result.action, "skip")

    async def test_queue_full_skips_low_priority(self):
        result = await self.mod.RuleGate.evaluate(FakeEvent("触发"), config(), queue_depth=200)
        self.assertEqual(result.reason, "queue_full")

    async def test_queue_full_allows_high_priority(self):
        event = FakeEvent("hello", components=[types.SimpleNamespace(qq="bot")])
        result = await self.mod.RuleGate.evaluate(event, config(), queue_depth=200)
        self.assertEqual(result.action, "candidate")

    async def test_cooldown_skips_after_reply(self):
        event = FakeEvent("触发")
        await self.mod.RuleGate.evaluate(event, config())
        self.mod.RuleGate.record_reply(event)
        self.mod.RuleGate._seen_message_ids.clear()
        result = await self.mod.RuleGate.evaluate(event, config())
        self.assertEqual(result.reason, "cooldown")

    async def test_duplicate_message_id_is_skipped(self):
        event = FakeEvent("触发")
        await self.mod.RuleGate.evaluate(event, config())
        result = await self.mod.RuleGate.evaluate(event, config())
        self.assertEqual(result.reason, "duplicate")

    async def test_question_after_bot_speech_is_candidate(self):
        with patch.object(
            self.history,
            "get_history_async",
            new=AsyncMock(return_value=[
                types.SimpleNamespace(sender=types.SimpleNamespace(user_id="bot"))
            ]),
        ):
            result = await self.mod.RuleGate.evaluate(FakeEvent("你觉得呢？"), config())
        self.assertEqual(result.trigger, "question")
