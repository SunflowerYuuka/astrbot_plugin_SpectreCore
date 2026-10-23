import copy
import unittest

from tests._orchestration_helpers import load_utils_module
from tests.test_rule_gate import FakeEvent, config


class CommandPrefixFilterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.legacy = load_utils_module("reply_decision").ReplyDecision
        self.gate = load_utils_module("rule_gate").RuleGate
        self.history = load_utils_module("history_storage").HistoryStorage
        self.llm = load_utils_module("llm_utils").LLMUtils
        self.cfg = config()
        self.cfg["model_frequency"].update(
            keywords=["help", "hello", "https", "选项"],
            ignored_prefixes=["/", "!"],
            blacklist_keywords=["拉黑"],
            probability={"probability": 1.0},
        )
        self.history.config = self.cfg
        self.llm.clear_call_status("p", False, "123")

    async def check_both_modes(self, text, expected):
        self.gate.reset_state()
        event = FakeEvent(text)
        self.assertEqual(self.legacy.should_reply(event, self.cfg), expected)
        result = await self.gate.evaluate(event, self.cfg)
        self.assertEqual(result.action == "candidate", expected)
        return result

    async def test_command_prefix_blocks_before_keyword_or_probability(self):
        for text in ["/help", "  /help", "\n\t/help", "!hello", "/unknown"]:
            with self.subTest(text=text):
                result = await self.check_both_modes(text, False)
                self.assertEqual(result.reason, "command_prefix")

    async def test_urls_and_slashes_inside_messages_are_allowed(self):
        for text in ["https://example.com/path", "看看 https://example.com", "这个选项是 开/关", "hello /help"]:
            with self.subTest(text=text):
                await self.check_both_modes(text, True)

    async def test_empty_prefixes_are_ignored_and_missing_config_keeps_old_behavior(self):
        self.cfg["model_frequency"]["ignored_prefixes"] = ["", "  ", None]
        await self.check_both_modes("hello", True)
        self.cfg["model_frequency"].pop("ignored_prefixes")
        await self.check_both_modes("/help", True)

    async def test_keyword_blacklist_still_matches_anywhere(self):
        await self.check_both_modes("hello 拉黑", False)
        self.cfg["model_frequency"]["blacklist_keywords"] = ["/"]
        await self.check_both_modes("https://example.com", False)

    async def test_filter_does_not_rewrite_message_or_config(self):
        before = copy.deepcopy(self.cfg)
        event = FakeEvent("  /help")
        self.assertFalse(self.legacy.should_reply(event, self.cfg))
        self.assertEqual(event.get_message_outline(), "  /help")
        self.assertEqual(self.cfg, before)
