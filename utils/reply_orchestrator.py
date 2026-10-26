import asyncio
import inspect
import time
import uuid

from astrbot.api.all import MessageChain, logger

from .decision_protocol import build_wake_instruction, parse_decision
from .llm_utils import LLMUtils
from .outbound_queue import OutboundItem
from .provider_utils import ProviderUnavailable, provider_supports_images, resolve_provider
from .quote_utils import QuoteUtils
from .rule_gate import RuleGate
from .text_filter import TextFilter
from .wake_manager import WakeManager


class ReplyOrchestrator:
    def __init__(self, context, config, queue, provider_resolver=None):
        self.context = context
        self.config = config
        self.queue = queue
        self.provider_resolver = provider_resolver or self._default_resolver
        orch = config.get("reply_orchestration", {}) or {}
        self.timeout = max(1, orch.get("timeout_ms", 2000)) / 1000
        self.max_reply_messages = orch.get("max_reply_messages", 3)
        self.min_confidence = orch.get("min_confidence", 0.55)
        self._semaphore = asyncio.Semaphore(max(1, orch.get("max_parallel", 12)))

    async def _default_resolver(self, event, config):
        orch = config.get("reply_orchestration", {}) or {}
        return await resolve_provider(self.context, event.unified_msg_origin, orch.get("provider_id", ""))

    async def _generate(self, event, wake_context=False):
        async with self._semaphore:
            provider = self.provider_resolver(event, self.config)
            if inspect.isawaitable(provider):
                provider = await provider
            supports_images = provider_supports_images(provider)
            request = await LLMUtils.build_request(
                event, self.config, self.context,
                decision_mode=True, include_images=supports_images,
            )
            if wake_context:
                request.system_prompt += build_wake_instruction()
            return await provider.text_chat(
                prompt=request.prompt,
                session_id=request.session_id,
                image_urls=request.image_urls if supports_images else [],
                contexts=request.contexts,
                system_prompt=request.system_prompt,
                func_tool=None,
            )

    async def handle(self, event, gate_result, wake_context=False):
        LLMUtils.disable_default_llm(event)
        if RuleGate.reply_limit_reason(event, self.config):
            return
        if not LLMUtils.mark_in_progress(event, only_if_idle=True):
            return
        started = time.monotonic()
        reservation = None
        accepted = False
        claimed_wake_message = getattr(gate_result, "reason", None) == "wake"
        wake_key = None
        wake_limits = None
        wake_session_id = None
        wake_starts_session = False
        try:
            if wake_context:
                active_session = WakeManager.get_active_session(event)
                if active_session is not None:
                    wake_key = WakeManager.key_for_event(event)
                    wake_session_id, wake_limits = active_session
                elif not claimed_wake_message:
                    wake_key = WakeManager.key_for_event(event)
                    wake_limits = WakeManager.resolve_limits(event, self.config)
                    wake_session_id = uuid.uuid4().hex
                    wake_starts_session = True
            response = await asyncio.wait_for(self._generate(event, wake_context), self.timeout)
            elapsed = (time.monotonic() - (event.get_extra("spectrecore_received_at") or started)) * 1000
            logger.debug(f"回复生成 generation_ms={elapsed:.1f} queue_depth={getattr(self.queue, 'depth', 0)}")
            decision = parse_decision(
                getattr(response, "completion_text", "") or "",
                self.max_reply_messages, self.min_confidence,
            )
            if decision.action != "reply":
                logger.debug(f"决策忽略: {decision.reason}")
                return
            result = event.make_result()
            filter_config = dict(self.config)
            filter_config["read_air"] = False
            for text in decision.messages:
                filtered = TextFilter.process_model_text(text, filter_config)
                if filtered.strip():
                    result.message(filtered)
            if not result.chain:
                return
            QuoteUtils.apply(event, result, self.context)
            if not result.chain:
                return
            QuoteUtils.apply_default_quote(event, result, self.context)
            message_id = str(getattr(event.message_obj, "message_id", "") or uuid.uuid4().hex)
            reply_id = f"{event.unified_msg_origin}:{message_id}"
            now = time.time()
            item = OutboundItem(
                item_id=reply_id, reply_id=reply_id,
                account_key=f"{event.get_platform_name()}:{event.get_self_id()}",
                platform_id=event.get_platform_name(), self_id=str(event.get_self_id()),
                umo=event.unified_msg_origin,
                chat_id=str(event.get_sender_id() if event.is_private_chat() else event.get_group_id()),
                is_private=event.is_private_chat(), chain=MessageChain(chain=list(result.chain)),
                created_at=now, expires_at=now + self.queue.ttl_seconds,
                priority=gate_result.priority,
                wake_key=wake_key,
                wake_limits=wake_limits,
                wake_session_id=wake_session_id,
                wake_starts_session=wake_starts_session,
            )
            reservation = RuleGate.record_reply(event)
            LLMUtils.clear_in_progress(event)
            accepted = await self.queue.enqueue(item)
        except asyncio.TimeoutError:
            logger.warning("决策 provider 超时，忽略本条消息")
        except ProviderUnavailable as exc:
            logger.warning(f"决策 provider 不可用: {exc}")
        except Exception as exc:
            logger.error(f"决策编排失败: {type(exc).__name__}")
        finally:
            if reservation is not None and not accepted:
                RuleGate.undo_reply(event, reservation)
            LLMUtils.clear_in_progress(event)
