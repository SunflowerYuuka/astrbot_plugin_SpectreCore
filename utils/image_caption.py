from astrbot.api.all import *
from typing import Iterable, Optional
import asyncio
import os

class ImageCaptionUtils:
    """
    图片转述工具类

    用于调用大语言模型将图片转述为文本描述
    """

    # 保存context和config对象的静态变量
    context: Optional[Context] = None
    config: Optional[AstrBotConfig] = None
    # 图片描述缓存
    caption_cache: dict[str, str] = {}
    # 限制所有会话同时进行的图片转述数量，避免多群并发时压垮提供商
    _caption_semaphore: Optional[asyncio.Semaphore] = None
    _caption_locks: dict[str, asyncio.Lock] = {}
    _max_concurrency = 3

    @staticmethod
    def init(context: Context, config: AstrBotConfig):
        """初始化图片转述工具类，保存context和config引用"""
        ImageCaptionUtils.context = context
        ImageCaptionUtils.config = config
        concurrency = config.get("image_processing", {}).get(
            "image_caption_concurrency", 3
        )
        ImageCaptionUtils._max_concurrency = max(1, int(concurrency))
        ImageCaptionUtils._caption_semaphore = None
        ImageCaptionUtils._caption_locks = {}

    @staticmethod
    def _normalize_image(image: Optional[str]) -> Optional[str]:
        if not image:
            return None
        if image.startswith("file:///"):
            path = image[8:]
            if not os.path.exists(path):
                return None
            return path
        return image

    @classmethod
    def _get_caption_semaphore(cls) -> asyncio.Semaphore:
        if cls._caption_semaphore is None:
            cls._caption_semaphore = asyncio.Semaphore(cls._max_concurrency)
        return cls._caption_semaphore

    @classmethod
    def _get_caption_lock(cls, image: str) -> asyncio.Lock:
        lock = cls._caption_locks.get(image)
        if lock is None:
            lock = asyncio.Lock()
            cls._caption_locks[image] = lock
        return lock

    @staticmethod
    async def prefetch_captions(
        images: Iterable[str],
        umo: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        """并行预取一组图片的描述，让后续格式化直接命中缓存。"""
        config = ImageCaptionUtils.config
        if not config:
            return
        if not config.get("image_processing", {}).get("use_image_caption", False):
            return

        unique_images: list[str] = []
        seen: set[str] = set()
        for raw_image in images:
            image = ImageCaptionUtils._normalize_image(raw_image)
            if not image or image in seen:
                continue
            if image in ImageCaptionUtils.caption_cache:
                continue
            seen.add(image)
            unique_images.append(image)

        if not unique_images:
            return

        await asyncio.gather(
            *(
                ImageCaptionUtils.generate_image_caption(
                    image, umo=umo, timeout=timeout
                )
                for image in unique_images
            ),
            return_exceptions=True,
        )

    @staticmethod
    async def generate_image_caption(
            image: str, # 图片的base64编码或URL
            umo: Optional[str] = None, # unified_msg_origin，用于 UMO 路由
            timeout: int = 30
        ) -> Optional[str]:
        """
        为单张图片生成文字描述

        Args:
            image: 图片的base64编码或URL
            umo: unified_msg_origin，用于获取对应 UMO 的 provider
            timeout: 超时时间（秒）

        Returns:
            生成的图片描述文本，如果失败则返回None
        """
        image = ImageCaptionUtils._normalize_image(image)
        if not image:
            return None

        # 检查缓存
        if image in ImageCaptionUtils.caption_cache:
            logger.debug(f"命中图片描述缓存: {image[:50]}...")
            return ImageCaptionUtils.caption_cache[image]

        # 获取配置
        config = ImageCaptionUtils.config
        context = ImageCaptionUtils.context

        if not config or not context:
            logger.warning("ImageCaptionUtils 未初始化")
            return None

        # 检查是否已启用图片转述
        image_processing_config = config.get("image_processing", {})
        if not image_processing_config.get("use_image_caption", False):
            return None

        # 同一张图片在多个群同时出现时只请求一次
        async with ImageCaptionUtils._get_caption_lock(image):
            if image in ImageCaptionUtils.caption_cache:
                return ImageCaptionUtils.caption_cache[image]

            provider_id = image_processing_config.get("image_caption_provider_id", "")
            # 获取提供商，支持 UMO 路由
            if provider_id == "":
                provider = context.get_using_provider(umo=umo)
            else:
                provider = context.get_provider_by_id(provider_id)

            if not provider or not hasattr(provider, "text_chat"):
                 logger.warning(f"无法找到提供商: {provider_id if provider_id else '默认'}")
                 return None

            text_chat = getattr(provider, "text_chat")
            try:
                # 带超时控制的调用大模型进行图片转述
                async def call_llm():
                    return await text_chat(
                        prompt=image_processing_config.get("image_caption_prompt", "请直接简短描述这张图片"),
                        contexts=[],
                        image_urls=[image],
                        func_tool=None,
                        system_prompt=""
                    )

                # 使用asyncio.wait_for添加超时控制
                async with ImageCaptionUtils._get_caption_semaphore():
                    llm_response = await asyncio.wait_for(call_llm(), timeout=timeout)
                caption = llm_response.completion_text

                # 缓存结果
                if caption:
                     ImageCaptionUtils.caption_cache[image] = caption
                     logger.debug(f"缓存图片描述: {image[:50]}... -> {caption}")

                return caption
            except asyncio.TimeoutError:
                logger.warning(f"图片转述超时，超过了{timeout}秒")
                return None
            except Exception as e:
                logger.error(f"图片转述失败: {e}")
                return None
