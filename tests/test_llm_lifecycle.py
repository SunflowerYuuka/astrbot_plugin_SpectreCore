import time
import types
import unittest

from tests._orchestration_helpers import load_utils_module


class LlmLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.llm = load_utils_module("llm_utils").LLMUtils
        self.llm.init({"reply_orchestration": {"max_in_progress_ms": 10}})
        self.llm.clear_call_status("p", False, "g")

    def test_in_progress_expires(self):
        self.llm.set_llm_in_progress("p", False, "g")
        time.sleep(0.02)
        self.assertFalse(self.llm.is_llm_in_progress("p", False, "g"))

    def test_disable_default_llm_sets_flag(self):
        event = types.SimpleNamespace(call_llm=False)
        self.llm.disable_default_llm(event)
        self.assertTrue(event.call_llm)

    def test_mark_and_clear_uses_event_extra(self):
        event = types.SimpleNamespace(
            call_llm=False,
            unified_msg_origin="p:group:g",
            _extra={},
            get_platform_name=lambda: "p",
            is_private_chat=lambda: False,
            get_group_id=lambda: "g",
            set_extra=lambda k, v: event._extra.__setitem__(k, v),
            get_extra=lambda k: event._extra.get(k),
        )
        self.llm.mark_in_progress(event)
        self.assertTrue(self.llm.is_llm_in_progress("p", False, "g"))
        self.llm.clear_in_progress(event)
        self.assertFalse(self.llm.is_llm_in_progress("p", False, "g"))

    def test_old_event_cannot_clear_new_request(self):
        from tests.test_reply_orchestrator import FakeEvent
        old, new = FakeEvent("g"), FakeEvent("g")
        self.llm.mark_in_progress(old)
        self.llm.mark_in_progress(new)
        self.llm.clear_in_progress(old)
        self.assertTrue(self.llm.is_llm_in_progress("p", False, "g"))
        self.llm.clear_in_progress(new)
