import asyncio
import copy
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from tests._orchestration_helpers import ROOT, load_utils_module
from tests.test_reply_orchestrator import FakeEvent


class AutonomousReplyPromptTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_utils_module("llm_utils")
        self.persona = {"name": "test", "prompt": "原有人格", "_begin_dialogs_processed": [
            {"role": "user", "content": "示例对话"}
        ]}
        self.context = types.SimpleNamespace(persona_manager=types.SimpleNamespace(
            get_default_persona_v3=AsyncMock(return_value=self.persona)
        ))
        self.config = {"smart_quote": False, "read_air": True,
                       "autonomous_reply_prompt": {"enabled": True, "text": "不要把群友的话默认当成对你说。\n先核对图片发送者。"}}

    async def build(self, config=None, model=False):
        event = FakeEvent()
        event.get_message_outline = lambda: "当前群消息"
        event.request_llm = lambda **kwargs: types.SimpleNamespace(**kwargs)
        with patch.object(self.mod.HistoryStorage, "get_history_async", new=AsyncMock(return_value=[])):
            if model:
                return await self.mod.LLMUtils.build_request(event, config or self.config, self.context, decision_mode=True)
            return await self.mod.LLMUtils.call_llm(event, config or self.config, self.context)

    async def test_legacy_adds_prompt_but_preserves_read_air_and_persona(self):
        request = await self.build()
        self.assertIn(self.config["autonomous_reply_prompt"]["text"], request.system_prompt)
        self.assertTrue(request.system_prompt.startswith("原有人格"))
        self.assertIn("<NO_RESPONSE>", request.system_prompt)
        self.assertEqual(request.prompt, "当前群消息")
        self.assertEqual(request.contexts, [{"role": "user", "content": "示例对话"}])

    async def test_model_retains_decision_protocol_after_custom_prompt(self):
        request = await self.build(model=True)
        self.assertIn(self.config["autonomous_reply_prompt"]["text"], request.system_prompt)
        self.assertLess(request.system_prompt.index("先核对图片发送者"), request.system_prompt.index("[临时决策协议]"))
        self.assertIn('"action":"ignore|reply"', request.system_prompt)

    async def test_disabled_missing_or_blank_prompt_has_no_effect(self):
        baseline = await self.build({"smart_quote": False, "read_air": True})
        for setting in [{"enabled": False, "text": "不应出现"},
                        {"enabled": True, "text": " \n\t "}, {"enabled": True}]:
            with self.subTest(setting=setting):
                request = await self.build({"smart_quote": False, "read_air": True, "autonomous_reply_prompt": setting})
                self.assertEqual(request.system_prompt, baseline.system_prompt)

    async def test_concurrent_and_later_requests_do_not_mutate_shared_persona_or_config(self):
        persona_before, config_before = copy.deepcopy(self.persona), copy.deepcopy(self.config)
        first, second = await asyncio.gather(self.build(), self.build(model=True))
        for request in (first, second):
            self.assertEqual(request.system_prompt.count("先核对图片发送者"), 1)
        self.assertEqual(self.persona, persona_before)
        self.assertEqual(self.config, config_before)
        later = await self.build({"smart_quote": False})
        self.assertNotIn("先核对图片发送者", later.system_prompt)

    def test_config_defaults_to_disabled_and_empty_multiline_text(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
        items = schema["autonomous_reply_prompt"]["items"]
        self.assertFalse(items["enabled"]["default"])
        self.assertEqual(items["text"]["default"], "")
        self.assertEqual(items["text"]["type"], "text")
