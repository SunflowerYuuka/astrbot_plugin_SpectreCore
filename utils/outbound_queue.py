import asyncio
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

from astrbot.api.all import logger


@dataclass
class OutboundItem:
    item_id: str
    account_key: str
    platform_id: str
    self_id: str
    umo: str
    chat_id: str
    is_private: bool
    chain: object
    created_at: float
    expires_at: float
    priority: int
    reply_id: str
    attempts: int = 0
    not_before: float = 0.0


class OutboundQueue:
    def __init__(self, config, sender, history_saver):
        q = (config.get("reply_orchestration", {}) or {}).get("outbound_queue", {}) or {}
        self.config = config
        self.enabled = bool(q.get("enabled", False))
        self.global_interval = max(0.001, q.get("global_interval_ms", 800) / 1000)
        self.group_interval = max(0.0, q.get("per_group_interval_ms", 6000) / 1000)
        self.jitter = max(0.0, q.get("jitter_ms", 300) / 1000)
        self.max_burst = max(1, q.get("max_burst", 3))
        self.ttl_seconds = max(0.0, q.get("ttl_ms", 20000) / 1000)
        self.max_pending = max(1, q.get("max_pending_per_group", 2))
        self.max_queue = max(1, q.get("max_queue_size", 200))
        self.retries = max(0, q.get("send_retries", 2))
        self.sender = sender
        self.history_saver = history_saver
        self._items: list[OutboundItem] = []
        self._reply_ids: dict[str, float] = {}
        self._reply_ttl = 300.0
        self._group_pending: Counter = Counter()
        self._group_next: dict[tuple[str, str], float] = {}
        self._tokens: dict[str, float] = defaultdict(lambda: float(self.max_burst))
        self._token_at: dict[str, float] = defaultdict(time.time)
        self._wake = asyncio.Event()
        self._task = None
        self._running = False

    @property
    def depth(self):
        return len(self._items)

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._worker())

    async def stop(self):
        self._running = False
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        dropped = len(self._items)
        self._items.clear()
        self._group_pending.clear()
        logger.info(f"发送队列已停止，丢弃 {dropped} 条未发送消息")

    def _prune_reply_ids(self, now: float) -> None:
        expired = [rid for rid, ts in self._reply_ids.items() if now - ts > self._reply_ttl]
        for rid in expired:
            self._reply_ids.pop(rid, None)

    def _can_send(self, item: OutboundItem) -> bool:
        now = time.time()
        mute = self.config.get("_temp_mute", {}) or {}
        if mute.get("until", 0) > now:
            return False
        chat_id = str(item.chat_id).strip()
        if item.is_private:
            return bool(self.config.get("enabled_private", False))
        blocked = {
            str(g).strip()
            for g in self.config.get("blocked_groups", [])
            if str(g).strip()
        }
        if chat_id in blocked:
            return False
        if self.config.get("enable_all_groups", False):
            return True
        enabled = {
            str(g).strip()
            for g in self.config.get("enabled_groups", [])
            if str(g).strip()
        }
        return chat_id in enabled

    async def _deliver(self, item: OutboundItem, save_history: bool = True) -> bool:
        logger.debug(f"队列发送 item={item.item_id} queue_wait_ms={(time.time() - item.created_at) * 1000:.1f} attempts={item.attempts}")
        try:
            ok = await self.sender(item.umo, item.chain)
        except Exception as exc:
            logger.warning(f"发送失败 {item.item_id}: {exc}")
            return False
        if ok and save_history:
            try:
                await self.history_saver(item)
            except Exception as exc:
                logger.error(f"保存 bot 历史失败 {item.item_id}: {exc}")
        return bool(ok)

    async def enqueue(self, item):
        now = time.time()
        self._prune_reply_ids(now)
        if item.expires_at <= now or item.reply_id in self._reply_ids:
            logger.debug(f"队列丢弃 item={item.item_id} reason=expired_or_duplicate")
            return False

        if not self.enabled:
            if not self._can_send(item):
                return False
            self._reply_ids[item.reply_id] = now
            return await self._deliver(item)

        key = (item.account_key, item.chat_id)
        if self._group_pending[key] >= self.max_pending:
            logger.info(f"单群待发送达到上限，丢弃 {item.item_id}")
            return False
        if len(self._items) >= self.max_queue:
            if item.priority < 2:
                logger.debug(f"队列丢弃 item={item.item_id} reason=queue_full")
                return False
            victims = [i for i in self._items if i.priority < 2]
            if not victims:
                return False
            victim = min(victims, key=lambda i: (i.priority, i.created_at))
            self._items.remove(victim)
            self._group_pending[(victim.account_key, victim.chat_id)] -= 1
            self._reply_ids.pop(victim.reply_id, None)

        item.not_before = now + (
            random.uniform(0, self.jitter) if self.jitter > 0 else 0.0
        )
        self._reply_ids[item.reply_id] = now
        self._group_pending[key] += 1
        self._items.append(item)
        self._wake.set()
        return True

    async def _worker(self):
        while self._running:
            if not self._items:
                self._wake.clear()
                await self._wake.wait()
                continue
            now = time.time()
            item = min(
                self._items,
                key=lambda i: (self._due_at(i, now), -i.priority, i.created_at),
            )
            due = self._due_at(item, now)
            if due > now:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=due - now)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            self._items.remove(item)
            self._group_pending[(item.account_key, item.chat_id)] -= 1
            try:
                await self._send(item)
            except Exception as exc:
                logger.error(f"发送队列处理异常 {item.item_id}: {exc}")

    def _due_at(self, item, now):
        key = (item.account_key, item.chat_id)
        group_at = self._group_next.get(key, 0)
        self._refill(item.account_key, now)
        token_at = now if self._tokens[item.account_key] >= 1 else now + (
            1 - self._tokens[item.account_key]
        ) * self.global_interval
        return max(now, group_at, token_at, item.not_before)

    def _refill(self, account, now):
        last = self._token_at[account]
        self._tokens[account] = min(
            self.max_burst,
            self._tokens[account] + (now - last) / self.global_interval,
        )
        self._token_at[account] = now

    async def _send(self, item):
        if item.expires_at <= time.time():
            logger.debug(f"队列丢弃 item={item.item_id} reason=expired")
            return
        if not self._can_send(item):
            logger.info(f"发送前硬约束不满足，丢弃 {item.item_id}")
            return
        now = time.time()
        self._refill(item.account_key, now)
        self._tokens[item.account_key] = max(0, self._tokens[item.account_key] - 1)
        self._token_at[item.account_key] = now
        self._group_next[(item.account_key, item.chat_id)] = now + self.group_interval
        if await self._deliver(item):
            return
        item.attempts += 1
        if item.attempts <= self.retries and time.time() < item.expires_at:
            key = (item.account_key, item.chat_id)
            if len(self._items) >= self.max_queue or self._group_pending[key] >= self.max_pending:
                logger.warning(f"发送重试丢弃 item={item.item_id} reason=queue_full")
                return
            item.not_before = time.time() + min(2 ** item.attempts, 8)
            self._group_pending[(item.account_key, item.chat_id)] += 1
            self._items.append(item)
            self._wake.set()
        else:
            logger.warning(f"发送失败丢弃 item={item.item_id} attempts={item.attempts}")
