import asyncio
import time
import types
import unittest

from tests._orchestration_helpers import load_utils_module


class FakeSender:
    def __init__(self):
        self.sent = []
        self.event = asyncio.Event()

    async def __call__(self, umo, chain):
        self.sent.append((time.monotonic(), umo, chain))
        self.event.set()
        return True


class QueueTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = load_utils_module("outbound_queue")
        self.sender = FakeSender()
        self.saved = []
        self.saved_event = asyncio.Event()
        self.config = {
            "enable_all_groups": True,
            "reply_orchestration": {"outbound_queue": {
                "enabled": True, "global_interval_ms": 20, "per_group_interval_ms": 20,
                "jitter_ms": 0, "max_burst": 1, "ttl_ms": 1000,
                "max_pending_per_group": 2, "max_queue_size": 10, "send_retries": 0,
            }},
        }
        self.queue = self.mod.OutboundQueue(self.config, self.sender, self._save)
        await self.queue.start()

    async def asyncTearDown(self):
        await self.queue.stop()

    async def _save(self, item):
        self.saved.append(item.item_id)
        self.saved_event.set()

    def _item(self, item_id, chat="1", reply_id=None, expires=None):
        now = time.time()
        return self.mod.OutboundItem(
            item_id=item_id, account_key="p:bot", platform_id="p", self_id="bot",
            umo="p:group:1", chat_id=chat, is_private=False,
            chain=types.SimpleNamespace(chain=[]), created_at=now,
            expires_at=expires if expires is not None else now + 5,
            priority=1, reply_id=reply_id or item_id,
        )

    async def test_sends_and_saves_history(self):
        await self.queue.enqueue(self._item("a"))
        await asyncio.wait_for(self.saved_event.wait(), 1)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.saved, ["a"])

    async def test_dedup_reply_id(self):
        await self.queue.enqueue(self._item("a", reply_id="r1"))
        await self.queue.enqueue(self._item("b", reply_id="r1"))
        await asyncio.wait_for(self.saved_event.wait(), 1)
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.sender.sent), 1)

    async def test_expired_item_is_dropped(self):
        await self.queue.enqueue(self._item("a", expires=time.time() - 1))
        await asyncio.sleep(0.05)
        self.assertEqual(self.sender.sent, [])

    async def test_disabled_queue_sends_immediately(self):
        config = {
            "enable_all_groups": True,
            "reply_orchestration": {"outbound_queue": {"enabled": False}},
        }
        queue = self.mod.OutboundQueue(config, self.sender, self._save)
        await queue.enqueue(self._item("now"))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.saved, ["now"])

    async def test_same_account_and_group_obey_intervals(self):
        self.queue.global_interval = 0.04
        self.queue.group_interval = 0.08
        await self.queue.enqueue(self._item("a"))
        await self.queue.enqueue(self._item("b"))
        deadline = time.monotonic() + 1
        while len(self.saved) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertEqual(len(self.saved), 2)
        self.assertGreaterEqual(self.sender.sent[1][0] - self.sender.sent[0][0], 0.075)

    async def test_config_change_before_send_blocks_item(self):
        await self.queue.enqueue(self._item("blocked"))
        self.config["blocked_groups"] = ["1"]
        await asyncio.sleep(0.05)
        self.assertEqual(self.sender.sent, [])

    async def test_failure_retries_then_saves_once(self):
        attempts = 0
        async def flaky(umo, chain):
            nonlocal attempts
            attempts += 1
            return attempts > 1
        self.queue.sender = flaky
        self.queue.retries = 1
        await self.queue.enqueue(self._item("retry"))
        await asyncio.wait_for(self.saved_event.wait(), 3)
        self.assertEqual(attempts, 2)
        self.assertEqual(self.saved, ["retry"])
        self.assertEqual(self.queue.depth, 0)
        self.assertEqual(self.queue._group_pending[("p:bot", "1")], 0)
