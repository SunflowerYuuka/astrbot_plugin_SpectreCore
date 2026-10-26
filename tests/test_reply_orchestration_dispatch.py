import importlib.util
import sys
import tempfile
import types
import unittest
from unittest.mock import ANY, AsyncMock, patch

from tests._orchestration_helpers import ROOT, SimpleMessage, load_utils_module
from tests.test_reply_orchestrator import FakeEvent


def load_plugin():
    load_utils_module("reply_decision")
    api = sys.modules["astrbot.api.all"]
    def decorator(*args, **kwargs):
        def wrap(fn):
            fn.command = decorator
            return fn
        return wrap
    class Star:
        def __init__(self, context): self.context = context
    api.Star = Star
    api.register = decorator
    api.event_message_type = decorator
    api.EventMessageType = types.SimpleNamespace(GROUP_MESSAGE=1, PRIVATE_MESSAGE=2)
    api.__all__ += ["Star", "register", "event_message_type", "EventMessageType"]
    event = types.ModuleType("astrbot.api.event")
    event.filter = types.SimpleNamespace(**{name: decorator for name in (
        "after_message_sent", "on_llm_response", "on_decorating_result", "command_group", "permission_type")})
    event.filter.PermissionType = types.SimpleNamespace(ADMIN=1)
    sys.modules[event.__name__] = event
    provider = types.ModuleType("astrbot.api.provider")
    provider.LLMResponse = object
    sys.modules[provider.__name__] = provider
    package = types.ModuleType("spectrecore_plugin_test")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package
    utils = types.ModuleType(package.__name__ + ".utils")
    for module, names in {
        "history_storage": ["HistoryStorage"], "image_caption": ["ImageCaptionUtils"],
        "llm_utils": ["LLMUtils"], "reply_decision": ["ReplyDecision"],
        "rule_gate": ["GateResult", "RuleGate"], "reply_orchestrator": ["ReplyOrchestrator"],
        "outbound_queue": ["OutboundQueue"], "quote_utils": ["QuoteUtils"],
        "text_filter": ["TextFilter"], "wake_manager": ["WakeManager"],
    }.items():
        for name in names:
            setattr(utils, name, getattr(load_utils_module(module), name))
    sys.modules[utils.__name__] = utils
    spec = importlib.util.spec_from_file_location(package.__name__ + ".main", ROOT / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DispatchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_plugin()
        self.mod.WakeManager.reset()
        self.mod.RuleGate.reset_state()
        self.plugin = self.mod.SpectreCore.__new__(self.mod.SpectreCore)
        self.plugin.config = {
            "enable_all_groups": True,
            "enabled_private": True,
            "reply_orchestration": {"enabled": True, "decision_mode": "model"},
        }
        self.mod.HistoryStorage.config = self.plugin.config
        self.plugin.context = object()
        self.plugin.outbound_queue = types.SimpleNamespace(depth=0)
        self.plugin.reply_orchestrator = types.SimpleNamespace(handle=AsyncMock())
        self.event = FakeEvent()
        self.event.get_message_outline = lambda: "hello"

    async def test_model_skip_suppresses_default_llm(self):
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.RuleGate, "evaluate", new=AsyncMock(return_value=types.SimpleNamespace(action="skip", reason="no_trigger"))), \
             patch.object(self.mod.ReplyDecision, "should_reply", side_effect=AssertionError("legacy called")):
            results = [result async for result in self.plugin._process_message(self.event)]
        self.assertEqual(results, [])
        self.assertTrue(self.event.call_llm)
        self.plugin.reply_orchestrator.handle.assert_not_called()

    async def test_model_candidate_runs_orchestrator(self):
        gate = types.SimpleNamespace(action="candidate", reason="keyword")
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.RuleGate, "evaluate", new=AsyncMock(return_value=gate)):
            self.assertEqual([r async for r in self.plugin._process_message(self.event)], [])
        self.plugin.reply_orchestrator.handle.assert_awaited_once_with(
            self.event, gate, wake_context=False
        )

    async def test_model_candidate_keeps_wake_context_disabled_when_wake_is_off(self):
        gate = types.SimpleNamespace(action="candidate", reason="keyword")
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.RuleGate, "evaluate", new=AsyncMock(return_value=gate)):
            self.assertEqual([r async for r in self.plugin._process_message(self.event)], [])
        self.plugin.reply_orchestrator.handle.assert_awaited_once_with(
            self.event, gate, wake_context=False
        )

    async def test_model_candidate_preserves_existing_wake_context_when_wake_is_off(self):
        manager = self.mod.WakeManager
        manager.activate(
            manager.key_for_event(self.event),
            load_utils_module("wake_manager").WakeLimits(300, 3, 120),
        )
        gate = types.SimpleNamespace(action="candidate", reason="keyword")
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.RuleGate, "evaluate", new=AsyncMock(return_value=gate)):
            self.assertEqual([r async for r in self.plugin._process_message(self.event)], [])
        self.plugin.reply_orchestrator.handle.assert_awaited_once_with(
            self.event, gate, wake_context=True
        )

    async def test_model_candidate_enables_wake_context_when_wake_is_on(self):
        self.plugin.config["reply_orchestration"]["wake"] = {"enabled": True}
        gate = types.SimpleNamespace(action="candidate", reason="keyword")
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.RuleGate, "evaluate", new=AsyncMock(return_value=gate)):
            self.assertEqual([r async for r in self.plugin._process_message(self.event)], [])
        self.plugin.reply_orchestrator.handle.assert_awaited_once_with(
            self.event, gate, wake_context=True
        )

    async def test_legacy_trigger_uses_json_orchestrator_when_wake_enabled(self):
        self.plugin.config = {
            "reply_orchestration": {
                "enabled": False,
                "decision_mode": "legacy",
                "wake": {"enabled": True},
            }
        }
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.ReplyDecision, "should_reply", return_value=True), \
             patch.object(self.mod.ReplyDecision, "process_and_reply", new=AsyncMock()) as legacy:
            self.assertEqual([r async for r in self.plugin._process_message(self.event)], [])
        self.plugin.reply_orchestrator.handle.assert_awaited_once_with(self.event, ANY, wake_context=True)
        legacy.assert_not_awaited()

    async def test_legacy_trigger_keeps_legacy_path_when_wake_disabled(self):
        self.plugin.config = {
            "reply_orchestration": {"enabled": False, "decision_mode": "legacy"}
        }
        async def legacy(*args):
            yield "legacy-request"
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.ReplyDecision, "should_reply", return_value=True), \
             patch.object(self.mod.ReplyDecision, "process_and_reply", legacy):
            results = [r async for r in self.plugin._process_message(self.event)]
        self.assertEqual(results, ["legacy-request"])
        self.plugin.reply_orchestrator.handle.assert_not_awaited()

    async def test_constructor_wires_outbound_delivery_callback(self):
        context = types.SimpleNamespace(send_message=AsyncMock())
        config = {"reply_orchestration": {"wake": {"enabled": True}}}
        with patch.object(self.mod.LLMUtils, "init"), \
             patch.object(self.mod.HistoryStorage, "init"), \
             patch.object(self.mod.ImageCaptionUtils, "init"), \
             patch.object(self.mod, "OutboundQueue") as queue_type:
            plugin = self.mod.SpectreCore(context, config)
        self.assertIs(queue_type.call_args.kwargs["delivery_callback"].__self__, plugin)
        self.assertIs(queue_type.call_args.kwargs["delivery_callback"].__func__,
                      plugin._on_outbound_delivered.__func__)

    async def test_constructor_migrates_legacy_wake_group_dict_for_config_ui(self):
        context = types.SimpleNamespace(send_message=AsyncMock())
        config = {"reply_orchestration": {"wake": {"groups": {
            "123": {"duration_seconds": 600},
        }}}}
        with patch.object(self.mod.LLMUtils, "init"), \
             patch.object(self.mod.HistoryStorage, "init"), \
             patch.object(self.mod.ImageCaptionUtils, "init"), \
             patch.object(self.mod, "OutboundQueue"):
            self.mod.SpectreCore(context, config)
        self.assertEqual(config["reply_orchestration"]["wake"]["groups"], [
            {"__template_key": "group", "group_id": "123", "duration_seconds": "600"},
        ])

    async def test_active_wake_claims_and_routes_before_normal_trigger_logic(self):
        self.plugin.config = {
            "enable_all_groups": True,
            "enabled_private": True,
            "reply_orchestration": {
                "enabled": False,
                "decision_mode": "legacy",
                "wake": {"enabled": True, "max_messages": 3},
            }
        }
        self.mod.HistoryStorage.config = self.plugin.config
        manager = self.mod.WakeManager
        manager.reset()
        key = manager.key_for_event(self.event)
        manager.activate(key, manager.resolve_limits(self.event, self.plugin.config))
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.ReplyDecision, "should_reply", side_effect=AssertionError("legacy called")):
            [r async for r in self.plugin._process_message(self.event)]
            self.event.message_obj.message_id = "m2"
            [r async for r in self.plugin._process_message(self.event)]
        self.assertEqual(self.plugin.reply_orchestrator.handle.await_count, 2)
        self.assertTrue(manager.is_active(self.event))

    async def test_disabled_model_keeps_legacy_request(self):
        self.plugin.config["reply_orchestration"]["enabled"] = False
        async def legacy(*args): yield "legacy-request"
        with patch.object(self.mod.HistoryStorage, "process_and_save_user_message", new=AsyncMock()), \
             patch.object(self.mod.ReplyDecision, "should_reply", return_value=True), \
             patch.object(self.mod.ReplyDecision, "process_and_reply", legacy):
            self.assertEqual([r async for r in self.plugin._process_message(self.event)], ["legacy-request"])
        self.assertFalse(self.event.call_llm)


class PipelineIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_reply_sends_and_persists_bot_history(self):
        mod = load_plugin()
        provider = types.SimpleNamespace(text_chat=AsyncMock(return_value=types.SimpleNamespace(
            completion_text='{"action":"reply","messages":["reply"],"confidence":0.9}'
        )))
        context = types.SimpleNamespace(
            send_message=AsyncMock(return_value=True),
            get_using_provider_async=AsyncMock(return_value=provider),
        )
        config = {"enable_all_groups": True, "smart_quote": False,
                  "image_processing": {"enable_image_persistence": False},
                  "model_frequency": {"keywords": ["hello"]},
                  "reply_orchestration": {"enabled": True, "decision_mode": "model",
                                          "wake": {"enabled": True}}}
        plugin = mod.SpectreCore.__new__(mod.SpectreCore)
        plugin.context, plugin.config = context, config
        mod.WakeManager.reset()
        plugin.outbound_queue = mod.OutboundQueue(
            config, context.send_message, plugin._save_bot_item,
            delivery_callback=plugin._on_outbound_delivered,
        )
        plugin.reply_orchestrator = mod.ReplyOrchestrator(context, config, plugin.outbound_queue)
        event = FakeEvent("123")
        event.message_obj = SimpleMessage("hello")
        plain = sys.modules["astrbot.api.all"].Plain
        event.message_obj.message = [plain("hello")]
        event.get_message_outline = lambda: "hello"
        event.request_llm = lambda **kwargs: types.SimpleNamespace(session_id="", **kwargs)
        def make_result():
            result = types.SimpleNamespace(chain=[])
            result.message = lambda text: result.chain.append(plain(text))
            return result
        event.make_result = make_result
        mod.RuleGate.reset_state()
        mod.LLMUtils.init(config)
        history = mod.HistoryStorage
        old_config, old_path = history.config, history.base_storage_path
        with tempfile.TemporaryDirectory() as directory:
            history.config, history.base_storage_path = config, directory
            try:
                await plugin.initialize()
                self.assertEqual([r async for r in plugin._process_message(event)], [])
                messages = await history.get_history_async("p", False, "123")
                self.assertEqual([m.message_str for m in messages], ["hello", "reply"])
                context.send_message.assert_awaited_once()
                self.assertEqual(provider.text_chat.call_args.kwargs["prompt"], "hello")
                self.assertTrue(mod.WakeManager.is_active(event))
            finally:
                await plugin.terminate()
                history.config, history.base_storage_path = old_config, old_path
