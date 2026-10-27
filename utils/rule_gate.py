import random
import re
import time
from dataclasses import dataclass

from astrbot.api.all import AstrBotConfig, AstrMessageEvent, logger
from .history_storage import HistoryStorage
from .llm_utils import LLMUtils
from .reply_filters import matches_ignored_prefix


@dataclass(frozen=True)
class GateResult:
    action: str
    reason: str
    priority: int = 0
    trigger: str = "none"


_QUESTION_RE = re.compile(r"[?？]|^(吗|呢|怎么|为什么|如何|什么|谁|哪|几|多少|是不是|能不能|可不可以)")


class RuleGate:
    _last_reply_at: dict[str, float] = {}
    _reply_times: dict[str, list[float]] = {}
    _seen_message_ids: dict[str, float] = {}
    _seen_ttl = 300.0

    @staticmethod
    def _chat_key(event) -> str:
        return f"{event.get_platform_name()}:{int(event.is_private_chat())}:{event.get_group_id() or event.get_sender_id()}"

    @classmethod
    def reset_state(cls) -> None:
        cls._last_reply_at.clear()
        cls._reply_times.clear()
        cls._seen_message_ids.clear()

    @classmethod
    def record_reply(cls, event) -> float:
        key = cls._chat_key(event)
        now = time.time()
        cls._last_reply_at[key] = now
        cls._reply_times.setdefault(key, []).append(now)
        return now

    @classmethod
    def undo_reply(cls, event, reservation: float) -> None:
        key = cls._chat_key(event)
        times = cls._reply_times.get(key, [])
        if reservation in times:
            times.remove(reservation)
        cls._last_reply_at[key] = max(times, default=0)

    @classmethod
    def is_direct_address(cls, event) -> bool:
        self_id = str(event.get_self_id())
        for component in cls._components(event):
            if str(getattr(component, "qq", "")) == self_id:
                return True
            if str(getattr(component, "sender_id", "")) == self_id:
                return True
        return False

    @classmethod
    def reply_limit_reason(cls, event, config, bypass: bool = False) -> str | None:
        if bypass:
            return None
        key, now = cls._chat_key(event), time.time()
        orch = config.get("reply_orchestration", {}) or {}
        if now - cls._last_reply_at.get(key, 0) < orch.get("cooldown_ms", 3000) / 1000:
            return "cooldown"
        times = [t for t in cls._reply_times.get(key, []) if now - t < 60]
        cls._reply_times[key] = times
        if len(times) >= orch.get("max_replies_per_minute", 6):
            return "rate_limited"
        return None

    @staticmethod
    def _components(event):
        return getattr(getattr(event, "message_obj", None), "message", None) or []

    @classmethod
    def _hard_check(cls, event, config) -> GateResult | None:
        if not HistoryStorage.is_chat_enabled(event):
            return GateResult("skip", "chat_disabled")

        now = time.time()
        mute = config.get("_temp_mute", {}) or {}
        if mute and mute.get("until", 0) > now:
            return GateResult("skip", "muted")

        text = (event.get_message_outline() or "").strip()
        if not text:
            return GateResult("skip", "empty")

        if matches_ignored_prefix(text, config):
            return GateResult("skip", "command_prefix")

        if str(event.get_sender_id()) == str(event.get_self_id()):
            return GateResult("skip", "self")

        is_private = event.is_private_chat()
        chat_id = event.get_sender_id() if is_private else event.get_group_id()
        if LLMUtils.is_llm_in_progress(event.get_platform_name(), is_private, chat_id):
            return GateResult("skip", "in_progress")

        message_id = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
        message_key = f"{event.get_platform_name()}:{chat_id}:{message_id}"
        cls._seen_message_ids = {
            key: timestamp for key, timestamp in cls._seen_message_ids.items()
            if now - timestamp < cls._seen_ttl
        }
        if message_id and message_key in cls._seen_message_ids:
            return GateResult("skip", "duplicate")
        if message_id:
            cls._seen_message_ids[message_key] = now

        blacklist = config.get("model_frequency", {}).get("blacklist_keywords", [])
        if any(word in text for word in blacklist):
            return GateResult("skip", "blacklist_keyword")

        limited = cls.reply_limit_reason(
            event, config, bypass=cls.is_direct_address(event)
        )
        if limited:
            return GateResult("skip", limited)
        return None

    @classmethod
    def check_wake_message(cls, event, config: AstrBotConfig, queue_depth: int = 0) -> GateResult:
        hard_result = cls._hard_check(event, config)
        if hard_result is not None:
            return hard_result

        max_queue = (config.get("reply_orchestration", {}) or {}).get(
            "outbound_queue", {}
        ).get("max_queue_size", 200)
        if queue_depth >= max_queue:
            return GateResult("skip", "queue_full")
        return GateResult("candidate", "wake", priority=1, trigger="wake")

    @classmethod
    async def evaluate(cls, event, config: AstrBotConfig, queue_depth: int = 0) -> GateResult:
        hard_result = cls._hard_check(event, config)
        if hard_result is not None:
            return hard_result

        text = (event.get_message_outline() or "").strip()

        is_private = event.is_private_chat()
        chat_id = event.get_sender_id() if is_private else event.get_group_id()

        orch = config.get("reply_orchestration", {}) or {}

        trigger = None
        components = cls._components(event)
        if any(str(getattr(c, "qq", "")) == str(event.get_self_id()) for c in components):
            trigger = GateResult("candidate", "mentioned", 2, "mention")
        elif any(str(getattr(c, "sender_id", "")) == str(event.get_self_id()) for c in components):
            trigger = GateResult("candidate", "reply_to_bot", 2, "reply_to_bot")
        else:
            keywords = config.get("model_frequency", {}).get("keywords", [])
            if any(word in text for word in keywords):
                trigger = GateResult("candidate", "keyword", 1, "keyword")

        if trigger is None and _QUESTION_RE.search(text):
            history = await HistoryStorage.get_history_async(
                event.get_platform_name(), is_private, chat_id,
            )
            if any(
                str(getattr(getattr(m, "sender", None), "user_id", "")) == str(event.get_self_id())
                for m in history[-10:]
            ):
                trigger = GateResult("candidate", "question", 1, "question")

        if trigger is None:
            probability = config.get("model_frequency", {}).get("probability", {}).get("probability", 0.0)
            if probability > 0 and random.random() < probability:
                trigger = GateResult("candidate", "random", 0, "random")

        if trigger is None:
            return GateResult("skip", "no_trigger")

        max_queue = orch.get("outbound_queue", {}).get("max_queue_size", 200)
        if queue_depth >= max_queue and trigger.priority < 2:
            return GateResult("skip", "queue_full")
        return trigger
