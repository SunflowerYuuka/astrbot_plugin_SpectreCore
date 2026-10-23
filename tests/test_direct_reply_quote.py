import types
import unittest
from unittest.mock import AsyncMock, patch

from tests._orchestration_helpers import load_utils_module
from tests.test_reply_orchestrator import FakeEvent


class DirectReplyQuoteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.orch_mod = load_utils_module("reply_orchestrator")
        self.quote_mod = load_utils_module("quote_utils")
        self.orch_mod.RuleGate.reset_state()
        self.orch_mod.LLMUtils.init({})
        self.settings = {"reply_with_quote": True, "reply_with_mention": False}
        self.context = types.SimpleNamespace(get_config=lambda **kwargs: {
            "platform_settings": self.settings,
            "provider_settings": {"streaming_response": True},
        })
        self.event = FakeEvent()
        self.event.message_obj.sender = types.SimpleNamespace(user_id="u", nickname="User")
        self.event.get_sender_name = lambda: "User"
        self.event.get_message_type = lambda: self.quote_mod.MessageType.GROUP_MESSAGE
        self.event.get_message_outline = lambda: "question"
        self.event.clear_result = lambda: None
        def make_result():
            result = types.SimpleNamespace(chain=[])
            result.message = lambda text: result.chain.append(self.quote_mod.Plain(text))
            return result
        self.event.make_result = make_result
        self.items = []
        async def enqueue(item):
            self.items.append(item)
            return True
        self.queue = types.SimpleNamespace(ttl_seconds=20, enqueue=enqueue)
        self.provider = types.SimpleNamespace(text_chat=AsyncMock(return_value=types.SimpleNamespace(
            completion_text='{"action":"reply","messages":["answer"],"confidence":1}'
        )))

    async def reply(self):
        request = types.SimpleNamespace(prompt="question", system_prompt="s", contexts=[], image_urls=[], session_id="")
        orch = self.orch_mod.ReplyOrchestrator(
            self.context, {"smart_quote": False}, self.queue,
            provider_resolver=lambda *args: self.provider,
        )
        with patch.object(self.orch_mod.LLMUtils, "build_request", new=AsyncMock(return_value=request)):
            await orch.handle(self.event, types.SimpleNamespace(priority=1))
        self.assertEqual(len(self.items), 1)
        return self.items[0].chain.chain

    async def test_enabled_setting_quotes_trigger_even_without_smart_quote(self):
        chain = await self.reply()
        self.assertIsInstance(chain[0], self.quote_mod.Reply)
        self.assertEqual(chain[0].id, "m1")
        self.assertEqual(chain[-1].text, "answer")

    async def test_disabled_setting_does_not_add_quote(self):
        self.settings["reply_with_quote"] = False
        chain = await self.reply()
        self.assertFalse(any(isinstance(c, self.quote_mod.Reply) for c in chain))

    async def test_model_selected_quote_is_preserved_without_duplicate(self):
        self.event.set_extra(self.quote_mod.QuoteUtils.EXTRA_KEY, {
            "1": {"id": "history-id", "sender_id": "other", "sender_name": "Other", "content": "earlier"}
        })
        self.provider.text_chat.return_value.completion_text = '{"action":"reply","messages":["[引用:1] answer"],"confidence":1}'
        chain = await self.reply()
        quotes = [c for c in chain if isinstance(c, self.quote_mod.Reply)]
        self.assertEqual([q.id for q in quotes], ["history-id"])
        self.assertEqual(chain[-1].text, "answer")

    async def test_invalid_model_quote_falls_back_to_trigger(self):
        self.event.set_extra(self.quote_mod.QuoteUtils.EXTRA_KEY, {"1": {"id": "history-id"}})
        self.provider.text_chat.return_value.completion_text = '{"action":"reply","messages":["[引用:999] answer"],"confidence":1}'
        chain = await self.reply()
        self.assertEqual(chain[0].id, "m1")
        self.assertEqual(chain[-1].text, "answer")

    async def test_quote_honors_group_mention_setting(self):
        self.settings["reply_with_mention"] = True
        chain = await self.reply()
        self.assertIsInstance(chain[0], self.quote_mod.Reply)
        self.assertIsInstance(chain[1], self.quote_mod.At)
        self.assertEqual(chain[1].qq, "u")

    async def test_private_quote_does_not_add_mention(self):
        self.settings["reply_with_mention"] = True
        self.event.is_private_chat = lambda: True
        self.event.get_group_id = lambda: None
        self.event.get_message_type = lambda: self.quote_mod.MessageType.FRIEND_MESSAGE
        chain = await self.reply()
        self.assertIsInstance(chain[0], self.quote_mod.Reply)
        self.assertFalse(any(isinstance(c, self.quote_mod.At) for c in chain))

    async def test_missing_message_id_does_not_add_invalid_quote(self):
        self.event.message_obj.message_id = ""
        chain = await self.reply()
        self.assertFalse(any(isinstance(c, self.quote_mod.Reply) for c in chain))
