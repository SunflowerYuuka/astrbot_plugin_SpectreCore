import types
import unittest
from unittest.mock import AsyncMock, patch

from tests._orchestration_helpers import load_utils_module
from tests.test_reply_orchestrator import FakeEvent


class RequestBuilderTest(unittest.IsolatedAsyncioTestCase):
    async def test_decision_mode_limits_history_and_disables_tools_and_images(self):
        mod = load_utils_module("llm_utils")
        event = FakeEvent()
        event.message_obj.message = []
        event.request_llm = lambda **kwargs: types.SimpleNamespace(**kwargs)
        context = types.SimpleNamespace(get_llm_tool_manager=lambda: self.fail("tools enabled"))
        history = [types.SimpleNamespace(message_id=f"h{i}", message=[]) for i in range(8)]
        config = {"smart_quote": False, "use_func_tool": True, "read_air": True,
                  "reply_orchestration": {"max_context_messages": 2}}
        with patch.object(mod.HistoryStorage, "get_history_async", new=AsyncMock(return_value=history)), \
             patch.object(mod.MessageUtils, "format_history_for_llm", new=AsyncMock(return_value="history")) as fmt, \
             patch.object(mod.MessageUtils, "outline_message_list", new=AsyncMock(return_value="question")), \
             patch.object(mod.ImageCaptionUtils, "prefetch_captions", new=AsyncMock()):
            request = await mod.LLMUtils.build_request(event, config, context, decision_mode=True, include_images=False)
        self.assertEqual(fmt.call_args.args[0], history[-2:])
        self.assertEqual(request.prompt, "question")
        self.assertIsNone(request.func_tool_manager)
        self.assertEqual(request.image_urls, [])
        self.assertIn("JSON", request.system_prompt)
        self.assertNotIn("<NO_RESPONSE>", request.system_prompt)

    async def test_legacy_keeps_read_air_and_tools(self):
        mod = load_utils_module("llm_utils")
        event = FakeEvent()
        event.get_message_outline = lambda: "hello"
        event.request_llm = lambda **kwargs: types.SimpleNamespace(**kwargs)
        context = types.SimpleNamespace(get_llm_tool_manager=lambda: "tools")
        with patch.object(mod.HistoryStorage, "get_history_async", new=AsyncMock(return_value=[])):
            request = await mod.LLMUtils.call_llm(event, {"smart_quote": False, "use_func_tool": True, "read_air": True}, context)
        self.assertEqual(request.func_tool_manager, "tools")
        self.assertIn("<NO_RESPONSE>", request.system_prompt)
