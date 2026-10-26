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

    def test_get_active_limits_returns_original_limits_and_expires_state(self):
        limits = self.mod.WakeLimits(100, 2, 30)
        key = self.manager.key_for_event(self.event)
        self.manager.activate(key, limits, now=100)
        self.assertEqual(self.manager.get_active_limits(self.event, now=110), limits)
        self.assertIsNone(self.manager.get_active_limits(self.event, now=131))

    def test_check_cleans_up_expired_state_for_other_chat(self):
        limits = self.mod.WakeLimits(100, 2, 30)
        active_key = self.manager.key_for_event(self.event)
        expired_key = "p:bot:group:other"
        self.manager.activate(active_key, limits, now=100)
        self.manager.activate(expired_key, self.mod.WakeLimits(5, 2, 0), now=100)

        self.assertTrue(self.manager.is_active(self.event, now=110))
        self.assertNotIn(expired_key, self.manager._states)


if __name__ == "__main__":
    unittest.main()
