from dataclasses import dataclass
import time
import uuid


@dataclass(frozen=True)
class WakeLimits:
    duration_seconds: int = 300
    max_messages: int = 5
    idle_timeout_seconds: int = 120


@dataclass
class WakeState:
    limits: WakeLimits
    started_at: float
    last_activity_at: float
    session_id: str
    consumed_messages: int = 0


class WakeManager:
    _states: dict[str, WakeState] = {}

    @staticmethod
    def key_for_event(event) -> str:
        chat_type = "private" if event.is_private_chat() else "group"
        chat_id = event.get_sender_id() if event.is_private_chat() else event.get_group_id()
        return f"{event.get_platform_name()}:{event.get_self_id()}:{chat_type}:{chat_id}"

    @classmethod
    def _scope_config(cls, event, config):
        wake = (config.get("reply_orchestration", {}) or {}).get("wake", {}) or {}
        if getattr(event, "is_admin", lambda: False)():
            return wake.get("admin", {}) or {}
        if event.is_private_chat():
            return wake.get("private", {}) or {}
        return (wake.get("groups", {}) or {}).get(str(event.get_group_id()), {}) or {}

    @classmethod
    def resolve_limits(cls, event, config) -> WakeLimits:
        wake = (config.get("reply_orchestration", {}) or {}).get("wake", {}) or {}
        override = cls._scope_config(event, config)
        return WakeLimits(
            int(override.get("duration_seconds", wake.get("duration_seconds", 300))),
            int(override.get("max_messages", wake.get("max_messages", 5))),
            int(override.get("idle_timeout_seconds", wake.get("idle_timeout_seconds", 120))),
        )

    @classmethod
    def enabled(cls, config) -> bool:
        return bool(((config.get("reply_orchestration", {}) or {}).get("wake", {}) or {}).get("enabled", False))

    @classmethod
    def activate(cls, key, limits, now=None, session_id=None):
        now = time.time() if now is None else now
        state = cls._states.get(key)
        if state is not None and not cls._is_expired(state, now):
            state.limits = limits
            state.last_activity_at = now
            return state.session_id
        session_id = session_id or uuid.uuid4().hex
        cls._states[key] = WakeState(limits, now, now, session_id)
        return session_id

    @classmethod
    def _is_expired(cls, state, now) -> bool:
        limits = state.limits
        return bool(
            (limits.duration_seconds and now - state.started_at >= limits.duration_seconds)
            or (limits.idle_timeout_seconds and now - state.last_activity_at >= limits.idle_timeout_seconds)
            or (limits.max_messages and state.consumed_messages >= limits.max_messages)
        )

    @classmethod
    def is_active(cls, event, now=None) -> bool:
        now = time.time() if now is None else now
        cls.clear_expired(now)
        key = cls.key_for_event(event)
        return key in cls._states

    @classmethod
    def get_active_limits(cls, event, now=None) -> WakeLimits | None:
        now = time.time() if now is None else now
        cls.clear_expired(now)
        key = cls.key_for_event(event)
        state = cls._states.get(key)
        return state.limits if state is not None else None

    @classmethod
    def get_active_session(cls, event, now=None) -> tuple[str, WakeLimits] | None:
        now = time.time() if now is None else now
        cls.clear_expired(now)
        state = cls._states.get(cls.key_for_event(event))
        if state is None:
            return None
        return state.session_id, state.limits

    @classmethod
    def record_delivery(cls, key, limits, session_id, starts_session=False, now=None) -> bool:
        now = time.time() if now is None else now
        state = cls._states.get(key)
        if state is not None and cls._is_expired(state, now):
            cls._states.pop(key, None)
            state = None

        if state is not None:
            if state.session_id != session_id:
                return False
            state.limits = limits
            state.last_activity_at = now
            return True

        if not starts_session:
            return False
        cls._states[key] = WakeState(limits, now, now, session_id)
        return True

    @classmethod
    def claim_message(cls, event, config, now=None) -> bool:
        now = time.time() if now is None else now
        cls.clear_expired(now)
        if not cls.enabled(config):
            return False
        state = cls._states.get(cls.key_for_event(event))
        if state is None:
            return False
        state.consumed_messages += 1
        state.last_activity_at = now
        return True

    @classmethod
    def clear_expired(cls, now=None):
        now = time.time() if now is None else now
        expired = [key for key, state in cls._states.items() if cls._is_expired(state, now)]
        for key in expired:
            cls._states.pop(key, None)

    @classmethod
    def reset(cls):
        cls._states.clear()
