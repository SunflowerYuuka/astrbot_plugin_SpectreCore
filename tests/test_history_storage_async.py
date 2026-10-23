import asyncio
import inspect
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests._orchestration_helpers import SimpleMessage, load_utils_module


class HistoryStorageAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = load_utils_module("history_storage").HistoryStorage
        self.history.base_storage_path = self.tmp.name
        self.history.config = {"image_processing": {"enable_image_persistence": False}}
        self.history._chat_locks.clear()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_concurrent_saves_do_not_lose_messages(self):
        await asyncio.gather(*[
            self.history.save_message(
                SimpleMessage(f"m{i}"), "test", chat_id_override="123"
            )
            for i in range(20)
        ])
        result = await self.history.get_history_async("test", False, "123")
        self.assertEqual(len(result), 20)

    async def test_reset_waits_for_pending_save(self):
        await self.history.save_message(SimpleMessage("old"), "test")
        entered, release = threading.Event(), threading.Event()
        write = self.history._write_history
        def blocked_write(*args):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test did not release writer")
            write(*args)
        with patch.object(self.history, "_write_history", blocked_write):
            save = asyncio.create_task(self.history.save_message(SimpleMessage("new"), "test"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                reset = self.history.clear_history("test", False, "123")
                if inspect.isawaitable(reset):
                    reset = asyncio.create_task(reset)
            finally:
                release.set()
            self.assertTrue(await save)
            self.assertTrue(await reset if isinstance(reset, asyncio.Task) else reset)
        self.assertEqual(await self.history.get_history_async("test", False, "123"), [])
