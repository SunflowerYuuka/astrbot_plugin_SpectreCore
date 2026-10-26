import asyncio
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_reply_orchestration_dispatch import load_plugin
from tests.test_reply_orchestrator import FakeEvent
from tests._orchestration_helpers import load_utils_module


class WakeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_plugin()
        self.plugin = self.mod.SpectreCore.__new__(self.mod.SpectreCore)
        self.plugin.config = {
            "enable_all_groups": True,
            "enabled_private": True,
            "reply_orchestration": {
                "enabled": True,
                "decision_mode": "model",
                "wake": {"enabled": True, "max_messages": 3},
            }
        }
        self.mod.HistoryStorage.config = self.plugin.config
        self.mod.RuleGate.reset_state()
        self.plugin.context = object()
        self.plugin.outbound_queue = types.SimpleNamespace(depth=0)
        self.plugin.reply_orchestrator = types.SimpleNamespace(handle=AsyncMock())
        self.event = FakeEvent()
        self.event.get_message_outline = lambda: "hello"

    async def test_only_successful_delivery_activates_wake(self):
        manager = self.mod.WakeManager
        manager.reset()
        limits = manager.resolve_limits(self.event, self.plugin.config)
        outbound = load_utils_module("outbound_queue")
        item = outbound.OutboundItem(
            item_id="wake", reply_id="wake", account_key="p:bot", platform_id="p",
            self_id="bot", umo="p:GroupMessage:1", chat_id="1", is_private=False,
            chain=types.SimpleNamespace(chain=[]), created_at=time.time(),
            expires_at=time.time() + 5, priority=1,
            wake_key=manager.key_for_event(self.event), wake_limits=limits,
            wake_session_id="initial-session", wake_starts_session=True,
        )
        config = {"enable_all_groups": True, "reply_orchestration": {"outbound_queue": {"enabled": False}}}
        queue = outbound.OutboundQueue(
            config, AsyncMock(return_value=True), AsyncMock(return_value=True),
            delivery_callback=self.plugin._on_outbound_delivered,
        )
        await queue.enqueue(item)
        self.assertTrue(manager.is_active(self.event))

    async def test_failed_delivery_does_not_activate_wake(self):
        manager = self.mod.WakeManager
        manager.reset()
        limits = manager.resolve_limits(self.event, self.plugin.config)
        outbound = load_utils_module("outbound_queue")
        item = outbound.OutboundItem(
            item_id="wake-failed", reply_id="wake-failed", account_key="p:bot", platform_id="p",
            self_id="bot", umo="p:GroupMessage:1", chat_id="1", is_private=False,
            chain=types.SimpleNamespace(chain=[]), created_at=time.time(),
            expires_at=time.time() + 5, priority=1,
            wake_key=manager.key_for_event(self.event), wake_limits=limits,
            wake_session_id="initial-session", wake_starts_session=True,
        )
        config = {"enable_all_groups": True, "reply_orchestration": {"outbound_queue": {"enabled": False}}}
        queue = outbound.OutboundQueue(
            config, AsyncMock(return_value=False), AsyncMock(),
            delivery_callback=self.plugin._on_outbound_delivered,
        )
        await queue.enqueue(item)
        self.assertFalse(manager.is_active(self.event))

    async def test_wake_ignore_keeps_state_and_next_message_is_checked(self):
        manager = self.mod.WakeManager
        manager.reset()
        key = manager.key_for_event(self.event)
        wake_limits = load_utils_module("wake_manager").WakeLimits
        manager.activate(key, wake_limits(300, 3, 120), now=time.time())
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()):
            [r async for r in self.plugin._process_message(self.event)]
            self.event.message_obj.message_id = "m2"
            [r async for r in self.plugin._process_message(self.event)]
        self.assertTrue(manager.is_active(self.event))
        self.assertEqual(manager._states[key].consumed_messages, 2)
        self.assertEqual(self.plugin.reply_orchestrator.handle.await_count, 2)

    async def test_active_wake_hard_gate_skips_do_not_claim_but_no_trigger_message_does(self):
        manager = self.mod.WakeManager
        key = manager.key_for_event(self.event)
        limits = load_utils_module("wake_manager").WakeLimits(300, 3, 120)
        manager.activate(key, limits)
        self.plugin.config["model_frequency"] = {
            "ignored_prefixes": ["!"],
            "blacklist_keywords": ["blocked"],
        }
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()):
            for message_id, text, self_sender in (
                ("wake-command", "!help", "u"),
                ("wake-blacklist", "blocked content", "u"),
                ("wake-self", "ordinary text", "bot"),
            ):
                self.event.message_obj.message_id = message_id
                self.event.get_message_outline = lambda text=text: text
                self.event.get_sender_id = lambda self_sender=self_sender: self_sender
                [result async for result in self.plugin._process_message(self.event)]
                self.assertTrue(manager.is_active(self.event))
                self.assertEqual(manager._states[key].consumed_messages, 0)

            self.event.message_obj.message_id = "wake-valid-no-trigger"
            self.event.get_message_outline = lambda: "ordinary text without a normal trigger"
            self.event.get_sender_id = lambda: "u"
            [result async for result in self.plugin._process_message(self.event)]

        self.assertEqual(manager._states[key].consumed_messages, 1)
        self.plugin.reply_orchestrator.handle.assert_awaited_once()

    async def test_user_history_is_saved_before_wake_claim(self):
        manager = self.mod.WakeManager
        manager.activate(manager.key_for_event(self.event), manager.resolve_limits(self.event, self.plugin.config))
        saved = AsyncMock()
        def claim_after_save(event, config):
            self.assertEqual(saved.await_count, 1)
            return False
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=saved), \
             patch.object(self.mod.WakeManager, "claim_message", side_effect=claim_after_save):
            [r async for r in self.plugin._process_message(self.event)]
        self.assertEqual(saved.await_count, 1)

    async def test_delivery_without_wake_metadata_does_not_activate(self):
        manager = self.mod.WakeManager
        manager.reset()
        await self.plugin._on_outbound_delivered(types.SimpleNamespace(wake_key=None, wake_limits=None))
        self.assertFalse(manager.is_active(self.event))

    async def test_successful_delivery_refresh_preserves_consumed_count(self):
        manager = self.mod.WakeManager
        manager.reset()
        key = manager.key_for_event(self.event)
        wake_limits = load_utils_module("wake_manager").WakeLimits
        manager.activate(key, wake_limits(300, 3, 120), now=time.time())
        session_id = manager._states[key].session_id
        self.assertTrue(manager.claim_message(self.event, self.plugin.config))
        previous_activity = manager._states[key].last_activity_at
        await asyncio.sleep(0.001)
        await self.plugin._on_outbound_delivered(types.SimpleNamespace(
            wake_key=key, wake_limits=wake_limits(300, 3, 120),
            wake_session_id=session_id, wake_starts_session=False,
        ))
        self.assertEqual(manager._states[key].consumed_messages, 1)
        self.assertGreater(manager._states[key].last_activity_at, previous_activity)

    async def test_delayed_delivery_does_not_refresh_a_newer_session(self):
        manager = self.mod.WakeManager
        manager.reset()
        key = manager.key_for_event(self.event)
        wake_limits = load_utils_module("wake_manager").WakeLimits(300, 3, 120)
        manager.activate(key, wake_limits)
        old_session_id = manager._states[key].session_id
        delayed_item = types.SimpleNamespace(
            wake_key=key,
            wake_limits=wake_limits,
            wake_session_id=old_session_id,
            wake_starts_session=False,
        )

        manager.reset()
        manager.activate(key, wake_limits)
        new_session_id = manager._states[key].session_id
        previous_activity = manager._states[key].last_activity_at
        await asyncio.sleep(0.001)

        await self.plugin._on_outbound_delivered(delayed_item)

        self.assertEqual(manager._states[key].session_id, new_session_id)
        self.assertEqual(manager._states[key].last_activity_at, previous_activity)

    async def test_delayed_delivery_from_exhausted_session_does_not_reactivate_it(self):
        manager = self.mod.WakeManager
        manager.reset()
        key = manager.key_for_event(self.event)
        wake_limits = load_utils_module("wake_manager").WakeLimits(300, 2, 120)
        manager.activate(key, wake_limits)
        session_id = getattr(manager._states[key], "session_id", None)

        self.assertTrue(manager.claim_message(self.event, self.plugin.config))
        delayed_item = types.SimpleNamespace(
            wake_key=key,
            wake_limits=wake_limits,
            wake_session_id=session_id,
            wake_starts_session=False,
        )
        self.assertTrue(manager.claim_message(self.event, self.plugin.config))

        await self.plugin._on_outbound_delivered(delayed_item)

        self.assertFalse(manager.is_active(self.event))

    async def test_final_allowed_wake_reply_does_not_start_a_new_session(self):
        manager = self.mod.WakeManager
        manager.reset()
        key = manager.key_for_event(self.event)
        wake_module = load_utils_module("wake_manager")
        limits = wake_module.WakeLimits(300, 2, 120)
        manager.activate(key, limits)
        self.assertTrue(manager.claim_message(self.event, self.plugin.config))

        config = {
            "enable_all_groups": True,
            "reply_orchestration": {
                "enabled": True,
                "decision_mode": "model",
                "wake": {"enabled": True, "max_messages": 2},
                "outbound_queue": {"enabled": False},
                "max_replies_per_minute": 6,
                "cooldown_ms": 0,
            },
        }
        outbound = load_utils_module("outbound_queue")
        queue = outbound.OutboundQueue(
            config, AsyncMock(return_value=True), AsyncMock(return_value=True),
            delivery_callback=self.plugin._on_outbound_delivered,
        )
        provider = types.SimpleNamespace(text_chat=AsyncMock(return_value=types.SimpleNamespace(
            completion_text='{"action":"reply","messages":["final"],"confidence":0.9}'
        )))
        llm = load_utils_module("llm_utils").LLMUtils
        orchestrator_module = load_utils_module("reply_orchestrator")
        orch = orchestrator_module.ReplyOrchestrator(
            object(), config, queue, provider_resolver=lambda *args: provider
        )
        self.plugin.config = config
        self.plugin.outbound_queue = queue
        self.plugin.reply_orchestrator = orch
        request = types.SimpleNamespace(
            prompt="hello", session_id="", image_urls=[], contexts=[], system_prompt="system"
        )
        with patch.object(llm, "build_request", new=AsyncMock(return_value=request)), \
             patch.object(orchestrator_module.QuoteUtils, "apply", lambda *args: None), \
             patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()):
            [result async for result in self.plugin._process_message(self.event)]

        self.assertFalse(manager.is_active(self.event))
