import asyncio
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

from tests._orchestration_helpers import load_utils_module


class FakeEvent:
    def __init__(self, chat="1"):
        self.call_llm = False
        self._extra = {}
        self.chat = chat
        self.unified_msg_origin = f"p:GroupMessage:{chat}"
        self.message_obj = types.SimpleNamespace(message_id=f"m{chat}")

    def get_platform_name(self): return "p"
    def is_private_chat(self): return False
    def get_group_id(self): return self.chat
    def get_sender_id(self): return "u"
    def get_self_id(self): return "bot"
    def set_extra(self, k, v): self._extra[k] = v
    def get_extra(self, k, default=None): return self._extra.get(k, default)
    def make_result(self):
        result = types.SimpleNamespace(chain=[])
        result.message = lambda text: result.chain.append(types.SimpleNamespace(text=text))
        return result


class OrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_utils_module("reply_orchestrator")
        self.llm = load_utils_module("llm_utils").LLMUtils
        self.llm.init({})
        self.mod.RuleGate.reset_state()
        load_utils_module("wake_manager").WakeManager.reset()
        self.items = []
        async def enqueue(item):
            self.items.append(item)
            return True
        self.queue = types.SimpleNamespace(ttl_seconds=20, enqueue=enqueue)
        self.request = types.SimpleNamespace(prompt="p", contexts=[], system_prompt="s", image_urls=["img"], session_id="")
        self.provider = types.SimpleNamespace(text_chat=AsyncMock(return_value=types.SimpleNamespace(
            completion_text='{"action":"reply","messages":["hello","world"],"confidence":0.9}'
        )))
        self.build = patch.object(self.llm, "build_request", new=AsyncMock(return_value=self.request))
        self.build.start()
        self.addCleanup(self.build.stop)
        quote = patch.object(self.mod.QuoteUtils, "apply", lambda *args: None)
        quote.start()
        self.addCleanup(quote.stop)

    def orchestrator(self, **cfg):
        return self.mod.ReplyOrchestrator(None, {"reply_orchestration": cfg}, self.queue,
                                          provider_resolver=lambda *args: self.provider)

    def test_wake_instruction_requires_topic_awareness(self):
        protocol = load_utils_module("decision_protocol")
        text = protocol.build_wake_instruction()
        self.assertIn("不要因为刚才回复过就继续回复", text)
        self.assertIn("ignore", text)

    async def test_reply_combines_messages_and_clears_in_progress(self):
        event = FakeEvent()
        await self.orchestrator().handle(event, types.SimpleNamespace(priority=1))
        self.assertEqual(len(self.items), 1)
        self.assertEqual([c.text for c in self.items[0].chain.chain], ["hello", "world"])
        self.assertTrue(event.call_llm)
        self.assertFalse(self.llm.is_llm_in_progress("p", False, "1"))
        self.assertEqual(self.provider.text_chat.call_args.kwargs["image_urls"], [])

    async def test_wake_context_adds_instruction_and_carries_limits(self):
        wake = load_utils_module("wake_manager")
        event = FakeEvent()
        key = wake.WakeManager.key_for_event(event)
        limits = wake.WakeLimits(900, 4, 250)
        wake.WakeManager.activate(key, limits)
        session_id = wake.WakeManager.get_active_session(event)[0]
        orch = self.orchestrator()
        await orch.handle(event, types.SimpleNamespace(priority=1), wake_context=True)
        kwargs = self.provider.text_chat.call_args.kwargs
        self.assertIn("不要因为刚才回复过就继续回复", kwargs["system_prompt"])
        self.assertEqual(self.items[0].wake_key, key)
        self.assertEqual(self.items[0].wake_limits, limits)
        self.assertEqual(self.items[0].wake_session_id, session_id)
        self.assertFalse(self.items[0].wake_starts_session)

    async def test_initial_wake_candidate_carries_new_session_metadata(self):
        event = FakeEvent()
        await self.orchestrator(wake={"enabled": True}).handle(
            event, types.SimpleNamespace(priority=1), wake_context=True
        )
        item = self.items[0]
        self.assertEqual(item.wake_key, load_utils_module("wake_manager").WakeManager.key_for_event(event))
        self.assertIsNotNone(item.wake_limits)
        self.assertTrue(item.wake_session_id)
        self.assertTrue(item.wake_starts_session)

    async def test_non_wake_context_does_not_add_wake_instruction(self):
        await self.orchestrator().handle(FakeEvent(), types.SimpleNamespace(priority=1))
        self.assertNotIn("不要因为刚才回复过就继续回复",
                         self.provider.text_chat.call_args.kwargs["system_prompt"])

    async def test_invalid_json_does_not_send(self):
        self.provider.text_chat.return_value.completion_text = "invalid"
        await self.orchestrator().handle(FakeEvent(), types.SimpleNamespace(priority=1))
        self.assertEqual(self.items, [])

    async def test_timeout_includes_semaphore_wait(self):
        orch = self.orchestrator(max_parallel=1, timeout_ms=30)
        await orch._semaphore.acquire()
        try:
            with self.assertLogs("test.astrbot", level="WARNING"):
                await asyncio.wait_for(orch.handle(FakeEvent(), types.SimpleNamespace(priority=1)), 0.3)
        finally:
            orch._semaphore.release()
        self.assertEqual(self.items, [])
        self.provider.text_chat.assert_not_called()

    async def test_twelve_groups_generate_in_parallel(self):
        active = maximum = 0
        async def generate(**kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.8)
            active -= 1
            return types.SimpleNamespace(completion_text='{"action":"reply","messages":["hi"],"confidence":1}')
        self.provider.text_chat = generate
        orch = self.orchestrator(max_parallel=12, timeout_ms=2000)
        start = time.monotonic()
        await asyncio.gather(*(orch.handle(FakeEvent(str(i)), types.SimpleNamespace(priority=1)) for i in range(12)))
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(maximum, 12)
        self.assertEqual(len(self.items), 12)

    async def test_delivery_wait_reserves_reply_limit(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked_enqueue(item):
            entered.set()
            await release.wait()
            self.items.append(item)
            return True
        self.queue.enqueue = blocked_enqueue
        orch = self.orchestrator(max_replies_per_minute=1)
        first = asyncio.create_task(orch.handle(FakeEvent(), types.SimpleNamespace(priority=1)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(orch.handle(FakeEvent(), types.SimpleNamespace(priority=1)))
            await asyncio.sleep(0.02)
            self.assertEqual(self.provider.text_chat.call_count, 1)
        finally:
            release.set()
            await first
            if 'second' in locals(): await second

    async def test_same_chat_admission_is_atomic(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked_provider(**kwargs):
            entered.set()
            await release.wait()
            return types.SimpleNamespace(completion_text='{"action":"ignore"}')
        self.provider.text_chat = AsyncMock(side_effect=blocked_provider)
        orch = self.orchestrator()
        first = asyncio.create_task(orch.handle(FakeEvent(), types.SimpleNamespace(priority=1)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(orch.handle(FakeEvent(), types.SimpleNamespace(priority=1)))
            await asyncio.sleep(0.02)
            self.assertEqual(self.provider.text_chat.call_count, 1)
        finally:
            release.set()
            await first
            if 'second' in locals(): await second
