import types
import unittest
from unittest.mock import AsyncMock, patch

from tests._orchestration_helpers import load_utils_module
from tests.test_reply_orchestrator import FakeEvent
from tests.test_reply_orchestration_dispatch import load_plugin


class LegacyReadAirTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_air_disables_streaming_before_request_building(self):
        mod = load_utils_module("llm_utils")
        event = FakeEvent()
        event.set_extra("enable_streaming", True)
        request = object()
        async def build(*args, **kwargs):
            self.assertIs(event.get_extra("enable_streaming"), False)
            return request
        with patch.object(mod.LLMUtils, "build_request", side_effect=build):
            result = await mod.LLMUtils.call_llm(event, {"read_air": True}, object())
        self.assertIs(result, request)

    async def test_without_read_air_keeps_existing_streaming_choice(self):
        mod = load_utils_module("llm_utils")
        event = FakeEvent()
        event.set_extra("enable_streaming", True)
        with patch.object(mod.LLMUtils, "build_request", new=AsyncMock()):
            await mod.LLMUtils.call_llm(event, {"read_air": False}, object())
        self.assertIs(event.get_extra("enable_streaming"), True)

    async def test_read_air_can_build_required_quote_prompt_with_global_streaming(self):
        mod = load_utils_module("llm_utils")
        event = FakeEvent()
        event.get_message_outline = lambda: "question"
        event.request_llm = lambda **kwargs: types.SimpleNamespace(**kwargs)
        config = {"provider_settings": {"streaming_response": True},
                  "platform_settings": {"reply_with_quote": True}}
        context = types.SimpleNamespace(get_config=lambda **kwargs: config)
        with patch.object(mod.HistoryStorage, "get_history_async", new=AsyncMock(return_value=[])), \
             patch.object(mod.QuoteUtils, "_is_astrbot_supported", return_value=True):
            request = await mod.LLMUtils.call_llm(event, {"read_air": True}, context)
        self.assertIs(event.get_extra("enable_streaming"), False)
        self.assertIn("你的回复会引用一条消息", request.system_prompt)
        self.assertIn("<NO_RESPONSE>", request.system_prompt)
        targets = event.get_extra(mod.QuoteUtils.EXTRA_KEY)
        self.assertEqual(targets["1"]["id"], "m1")
        self.assertTrue(config["provider_settings"]["streaming_response"])

    async def test_read_air_silence_still_clears_result(self):
        plugin_mod = load_plugin()
        plugin = plugin_mod.SpectreCore.__new__(plugin_mod.SpectreCore)
        plugin.config = {"read_air": True}
        plugin.context = object()
        event = FakeEvent()
        quote_mod = load_utils_module("quote_utils")
        result = types.SimpleNamespace(chain=[quote_mod.Plain("<NO_RESPONSE>")], is_llm_result=lambda: True)
        event.get_result = lambda: result
        event.clear_result = lambda: result.chain.clear()
        await plugin.on_decorating_result(event)
        self.assertEqual(result.chain, [])
