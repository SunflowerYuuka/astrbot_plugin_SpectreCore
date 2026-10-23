from astrbot.api.all import *
from typing import Dict, List, Optional, Any
import time
import threading
import uuid
from .forward_utils import ForwardUtils, fetch_forward_via_event
from .decision_protocol import build_decision_instruction
from .history_storage import HistoryStorage
from .image_caption import ImageCaptionUtils
from .image_context import (
    ImageRegistry,
    build_image_instruction,
    collect_image_urls,
    select_context_urls,
    select_recent_urls,
)
from .message_utils import MessageUtils
from .quote_utils import QuoteUtils
from astrbot.core.provider.entites import ProviderRequest

class LLMUtils:
    """
    大模型调用工具类
    用于构建提示词和调用记录相关功能
    """
    
    # 使用字典保存每个聊天的大模型调用状态
    # 格式: {"{platform_name}_{chat_type}_{chat_id}": {"last_call_time": timestamp, "in_progress": True/False}}
    _llm_call_status: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()  # 用于线程安全的锁
    _max_in_progress_ms: int = 15000
    _IN_PROGRESS_EXTRA = "spectrecore_in_progress_key"

    @staticmethod
    def init(config: AstrBotConfig) -> None:
        orch = config.get("reply_orchestration", {}) or {}
        LLMUtils._max_in_progress_ms = int(orch.get("max_in_progress_ms", 15000))

    @staticmethod
    def get_chat_key(platform_name: str, is_private_chat: bool, chat_id: str) -> str:
        """
        获取聊天的唯一标识
        
        Args:
            platform_name: 平台名称
            is_private_chat: 是否为私聊
            chat_id: 聊天ID
            
        Returns:
            聊天的唯一标识
        """
        chat_type = "private" if is_private_chat else "group"
        return f"{platform_name}_{chat_type}_{chat_id}"
    
    @staticmethod
    def set_llm_in_progress(platform_name: str, is_private_chat: bool, chat_id: str, in_progress: bool = True) -> None:
        """
        设置大模型调用状态
        
        Args:
            platform_name: 平台名称
            is_private_chat: 是否为私聊
            chat_id: 聊天ID
            in_progress: 是否正在进行大模型调用
        """
        chat_key = LLMUtils.get_chat_key(platform_name, is_private_chat, chat_id)
        
        with LLMUtils._lock:
            data = LLMUtils._llm_call_status.setdefault(chat_key, {})
            data["in_progress"] = in_progress
            data["last_call_time"] = time.time()
            if in_progress:
                data["started_at"] = time.time()
            else:
                data.pop("started_at", None)
    
    @staticmethod
    def is_llm_in_progress(platform_name: str, is_private_chat: bool, chat_id: str) -> bool:
        """
        检查指定聊天是否正在进行大模型调用
        
        Args:
            platform_name: 平台名称
            is_private_chat: 是否为私聊
            chat_id: 聊天ID
            
        Returns:
            是否正在进行大模型调用
        """
        chat_key = LLMUtils.get_chat_key(platform_name, is_private_chat, chat_id)
        
        with LLMUtils._lock:
            data = LLMUtils._llm_call_status.get(chat_key)
            if not data or not data.get("in_progress", False):
                return False
            started_at = data.get("started_at", 0)
            if time.time() - started_at > LLMUtils._max_in_progress_ms / 1000:
                data["in_progress"] = False
                data.pop("started_at", None)
                return False
            return True

    @staticmethod
    def mark_in_progress(event: AstrMessageEvent, only_if_idle: bool = False) -> bool:
        is_private = event.is_private_chat()
        chat_id = event.get_sender_id() if is_private else event.get_group_id()
        key = LLMUtils.get_chat_key(event.get_platform_name(), is_private, chat_id)
        owner = uuid.uuid4().hex
        with LLMUtils._lock:
            data = LLMUtils._llm_call_status.setdefault(key, {})
            if only_if_idle and data.get("in_progress") and (
                time.time() - data.get("started_at", 0) <= LLMUtils._max_in_progress_ms / 1000
            ):
                return False
            data.update(in_progress=True, started_at=time.time(), last_call_time=time.time(), owner=owner)
        event.set_extra(LLMUtils._IN_PROGRESS_EXTRA, owner)
        return True

    @staticmethod
    def clear_in_progress(event: AstrMessageEvent) -> None:
        is_private = event.is_private_chat()
        chat_id = event.get_sender_id() if is_private else event.get_group_id()
        key = LLMUtils.get_chat_key(event.get_platform_name(), is_private, chat_id)
        owner = event.get_extra(LLMUtils._IN_PROGRESS_EXTRA)
        with LLMUtils._lock:
            data = LLMUtils._llm_call_status.get(key, {})
            if owner and data.get("owner") == owner:
                data["in_progress"] = False
                data.pop("started_at", None)
                data.pop("owner", None)
        event.set_extra(LLMUtils._IN_PROGRESS_EXTRA, None)

    @staticmethod
    def disable_default_llm(event: AstrMessageEvent) -> None:
        event.call_llm = True
    
    @staticmethod
    def get_last_call_time(platform_name: str, is_private_chat: bool, chat_id: str) -> Optional[float]:
        """
        获取指定聊天最后一次大模型调用的时间戳
        
        Args:
            platform_name: 平台名称
            is_private_chat: 是否为私聊
            chat_id: 聊天ID
            
        Returns:
            最后一次调用的时间戳，如果从未调用过则返回None
        """
        chat_key = LLMUtils.get_chat_key(platform_name, is_private_chat, chat_id)
        
        with LLMUtils._lock:
            if chat_key not in LLMUtils._llm_call_status:
                return None
                
            return LLMUtils._llm_call_status[chat_key].get("last_call_time")
    
    @staticmethod
    async def call_llm(event: AstrMessageEvent, config: AstrBotConfig, context: Context) -> ProviderRequest:
        if config.get("read_air", False):
            event.set_extra("enable_streaming", False)
        return await LLMUtils.build_request(event, config, context, decision_mode=False)

    @staticmethod
    async def build_request(
        event: AstrMessageEvent,
        config: AstrBotConfig,
        context: Context,
        *,
        decision_mode: bool = False,
        include_images: bool = True,
    ) -> ProviderRequest:
        """
        构建调用大模型的请求对象

        Args:
            event: 消息对象
            config: 配置对象
            context: Context 对象，用于获取LLM工具管理器
            decision_mode: 使用临时 JSON 决策提示词并减少历史上下文
            include_images: 是否把图片传给 provider

        Returns:
            ProviderRequest 对象
        """
        platform_name = event.get_platform_name()
        is_private = event.is_private_chat()
        chat_id = event.get_group_id() if not is_private else event.get_sender_id()

        # 准备并调用大模型
        func_tools_mgr = None if decision_mode else (
            context.get_llm_tool_manager() if config.get("use_func_tool", False) else None
        )

        # 使用 AstrBot 原生 UMO 人格机制获取人格
        system_prompt = ""
        contexts = []
        umo = event.unified_msg_origin

        try:
            # 遵循 AstrBot 原生人格获取优先级：
            # 1. session_service_config.persona_id (会话级别配置)
            # 2. 配置文件的 default_personality
            # 3. 全局默认人格

            persona_id = None
            persona = None

            # 优先级1: 查询会话级别的人格配置 (通过全局 SharedPreferences)
            try:
                from astrbot.api import sp
                session_config = await sp.get_async(
                    scope="umo", scope_id=umo, key="session_service_config", default={}
                )
                persona_id = session_config.get("persona_id")
                if persona_id:
                    logger.debug(f"从 session_service_config 获取人格: '{persona_id}'")
            except Exception as e:
                logger.debug(f"获取 session_service_config 失败: {e}")

            # 优先级2/3: 使用 get_default_persona_v3 获取配置文件或全局默认人格
            if not persona_id:
                if hasattr(context, 'persona_manager') and hasattr(context.persona_manager, 'get_default_persona_v3'):
                    persona = await context.persona_manager.get_default_persona_v3(umo=umo)
                    persona_id = persona.get('name') if persona else None
                else:
                    # Fallback: 旧版 AstrBot 兼容
                    persona = context.persona_manager.selected_default_persona_v3 if hasattr(context, 'persona_manager') else None
                    persona_id = persona.get('name') if persona else None

            # 根据 persona_id 获取完整的人格数据
            if persona_id and not persona:
                try:
                    persona = next(
                        (p for p in context.persona_manager.personas_v3 if p["name"] == persona_id),
                        None
                    )
                except Exception:
                    pass

            if persona:
                system_prompt = persona.get('prompt', '')
                if persona.get('_mood_imitation_dialogs_processed'):
                    mood_dialogs = persona.get('_mood_imitation_dialogs_processed', '')
                    if mood_dialogs:
                        system_prompt += "\n请模仿以下示例的对话风格来反应(示例中，a代表用户，b代表你)\n" + str(mood_dialogs)

                begin_dialogs = persona.get('_begin_dialogs_processed', [])
                if begin_dialogs:
                    contexts.extend(begin_dialogs)

                logger.debug(f"使用 UMO '{umo}' 对应的人格: '{persona.get('name', 'default')}'")
        except Exception as e:
            logger.error(f"获取人格信息失败: {e}")

        # 构建环境描述（注入到 system_prompt，不污染 prompt）
        env_description = f"\n\n你正在浏览聊天软件，你在聊天软件上的id是{event.get_self_id()}"

        # 对于aiocqhttp平台，尝试获取bot用户名
        if platform_name == "aiocqhttp" and hasattr(event, "bot"):
            try:
                bot = getattr(event, "bot")
                bot_name = (await bot.api.get_login_info())["nickname"]
                env_description += f"，用户名是{bot_name}"
            except Exception as e:
                logger.warning(f"通过 event.bot 获取机器人昵称失败: {e}")

        if is_private:
            sender_display_name = event.get_sender_name() if event.get_sender_name() else f"ID为 {event.get_sender_id()} 的人"
            env_description += f"，你正在和 {sender_display_name} 私聊页面中。"
        else:
            group_display_name = chat_id
            if platform_name in ["aiocqhttp", "gewechat"]:
                try:
                    group = await event.get_group()
                    if group and group.group_name:
                        group_display_name = f"{group.group_name}({chat_id})"
                except Exception as e:
                    logger.warning(f"为 {platform_name} 获取群组信息失败: {e}")
            env_description += f"，你正在群聊 {group_display_name} 中。"

        # 添加历史记录（文本格式，注入到 system_prompt）
        # 注意：基于 message_id 精确排除当前消息，避免重复
        history_limit = (
            config.get("reply_orchestration", {}).get("max_context_messages", 20)
            if decision_mode else config.get("group_msg_history", 10)
        )
        history_messages = await HistoryStorage.get_history_async(platform_name, is_private, chat_id)
        # 模型自主引用：为聊天记录中的消息和当前消息编号，供模型用 [引用:编号] 指定要引用的消息
        quote_mode = QuoteUtils.get_mode(event, config, context)
        quote_targets = {} if quote_mode else None
        current_target = None

        image_processing = config.get("image_processing", {})
        image_count = image_processing.get("image_count", 0)
        context_messages = image_processing.get("image_context_messages", 10)
        context_max_images = image_processing.get("image_context_max_images", 5)

        forward_processing = config.get("forward_processing", {}) or {}
        forward_utils = None
        if forward_processing.get("parse_forward", False):
            forward_utils = ForwardUtils(
                fetch_forward=lambda forward_id: fetch_forward_via_event(event, forward_id),
                max_depth=forward_processing.get("max_depth", 2),
                max_nodes=forward_processing.get("max_nodes", 50),
                max_chars=forward_processing.get("max_chars", 2000),
                fetch_timeout=forward_processing.get("fetch_timeout", 5),
            )

        image_registry = ImageRegistry()
        try:
            if history_messages:
                # 获取当前消息的 message_id 用于精确排除
                current_msg_id = getattr(event.message_obj, 'message_id', None) if hasattr(event, 'message_obj') else None
                if current_msg_id:
                    history_for_context = [m for m in history_messages if getattr(m, 'message_id', None) != current_msg_id]
                else:
                    # 回退到排除最后一条
                    history_for_context = history_messages[:-1] if len(history_messages) > 1 else []

                # 只对实际会展示给模型的历史消息做图片筛选，避免出现没有编号的孤立图片
                messages_for_display = history_for_context[-history_limit:] if history_limit > 0 else []

                if messages_for_display:
                    history_image_urls = [
                        collect_image_urls(getattr(message, "message", None))
                        for message in messages_for_display
                    ]
                    allowed_urls = set(select_recent_urls(history_image_urls, image_count))
                    allowed_urls.update(
                        select_context_urls(history_image_urls, context_messages, context_max_images)
                    )
                    image_registry = ImageRegistry(allowed_urls=allowed_urls)
                    await ImageCaptionUtils.prefetch_captions(allowed_urls, umo=umo)
                    formatted_history = await MessageUtils.format_history_for_llm(messages_for_display, max_messages=history_limit, umo=umo, quote_targets=quote_targets, image_registry=image_registry, forward_utils=forward_utils)
                    env_description += "\n\n以下是最近的聊天记录：\n" + formatted_history
                else:
                    env_description += "\n\n你没看见任何聊天记录，看来最近没有消息。"
            else:
                env_description += "\n\n你没看见任何聊天记录，看来最近没有消息。"
        except Exception as e:
            logger.error(f"获取或格式化历史记录失败: {e}")
            env_description += "\n\n你没看见任何聊天记录，看来最近没有消息。"

        # 行为指引
        env_description += "\n(在聊天记录中，你的用户名以AstrBot被代替了)"
        env_description += "\n(如果你想回复某人，不要使用类似 [At:id(昵称)]这样的格式)"
        if quote_mode:
            current_no = None
            current_target = QuoteUtils.make_target(event.message_obj, "")
            if current_target:
                current_no = str(len(quote_targets) + 1)
                quote_targets[current_no] = current_target
            if quote_targets:
                event.set_extra(QuoteUtils.EXTRA_KEY, quote_targets)
                env_description += QuoteUtils.build_instruction(quote_mode, current_no)

        if decision_mode:
            system_prompt += build_decision_instruction()
        elif config.get("read_air", False):
            env_description += "\n\n现在你收到了一条新消息，你的反应是:\n(如果你想发送一条消息，直接输出发送的内容，如果你选择忽略，直接输出<NO_RESPONSE>)"
        else:
            env_description += "\n\n现在你收到了一条新消息，你决定发送一条消息回复(你输出的内容将作为消息发送)"

        # 将环境描述追加到 system_prompt
        system_prompt += env_description

        # 历史图片已经完成筛选；当前消息及其引用消息中的图片始终附带
        image_registry.allow_all()

        # prompt 只保留用户当前消息，使用 MessageUtils 确保图片被转述
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "message"):
            await ImageCaptionUtils.prefetch_captions(
                collect_image_urls(event.message_obj.message), umo=umo
            )
            prompt = await MessageUtils.outline_message_list(event.message_obj.message, umo=umo, image_registry=image_registry, forward_utils=forward_utils)
        else:
            prompt = event.get_message_outline()
        if current_target:
            current_target["content"] = prompt

        image_urls = image_registry.urls if include_images else []
        system_prompt += build_image_instruction(len(image_urls))

        return event.request_llm(
            prompt=prompt,
            func_tool_manager=func_tools_mgr,
            contexts=contexts,
            system_prompt=system_prompt,
            image_urls=image_urls,
        )
    
    @staticmethod
    def clear_call_status(platform_name: str, is_private_chat: bool, chat_id: str) -> None:
        """
        清除指定聊天的大模型调用状态
        
        Args:
            platform_name: 平台名称
            is_private_chat: 是否为私聊
            chat_id: 聊天ID
        """
        chat_key = LLMUtils.get_chat_key(platform_name, is_private_chat, chat_id)
        
        with LLMUtils._lock:
            if chat_key in LLMUtils._llm_call_status:
                del LLMUtils._llm_call_status[chat_key] 
