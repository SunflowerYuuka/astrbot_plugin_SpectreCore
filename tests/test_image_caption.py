import asyncio
import importlib.util
import logging
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
UTILS = ROOT / "utils"
PACKAGE = "spectrecore_caption_under_test"


def _install_astrbot_stub():
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    all_module = types.ModuleType("astrbot.api.all")

    names = [
        "AstrBotConfig",
        "AstrBotMessage",
        "AstrMessageEvent",
        "At",
        "BaseMessageComponent",
        "Contact",
        "Context",
        "Face",
        "Forward",
        "Image",
        "Location",
        "MessageEventResult",
        "Music",
        "Node",
        "Nodes",
        "Poke",
        "Plain",
        "Record",
        "Reply",
        "Share",
        "Video",
    ]
    for name in names:
        setattr(all_module, name, type(name, (), {}))

    all_module.AstrBotConfig = dict
    all_module.Context = object
    all_module.AstrBotMessage = object
    all_module.AstrMessageEvent = object
    all_module.BaseMessageComponent = object
    all_module.logger = logging.getLogger("test.astrbot")
    all_module.__all__ = names + ["logger"]

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.all"] = all_module


def _load_package_module(name):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", UTILS / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载被测模块: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_install_astrbot_stub()
package = types.ModuleType(PACKAGE)
package.__path__ = [str(UTILS)]
sys.modules[PACKAGE] = package

image_context = _load_package_module("image_context")
image_caption = _load_package_module("image_caption")
_load_package_module("forward_utils")
_load_package_module("quote_utils")
message_utils = _load_package_module("message_utils")

ImageCaptionUtils = image_caption.ImageCaptionUtils
ImageRegistry = image_context.ImageRegistry
MessageUtils = message_utils.MessageUtils


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.delay = 0.02

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs["image_urls"][0])
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        return types.SimpleNamespace(completion_text="caption")


class FakeContext:
    def __init__(self, provider):
        self.provider = provider

    def get_using_provider(self, umo=None):
        return self.provider

    def get_provider_by_id(self, provider_id):
        return self.provider


class FakeImage:
    def __init__(self, url):
        self.type = "image"
        self.file = url
        self.url = ""


class ImageCaptionConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = FakeProvider()
        ImageCaptionUtils.caption_cache.clear()
        ImageCaptionUtils._caption_locks = {}
        ImageCaptionUtils._caption_semaphore = None
        ImageCaptionUtils.init(
            FakeContext(self.provider),
            {"image_processing": {"use_image_caption": True}},
        )

    def tearDown(self):
        ImageCaptionUtils.caption_cache.clear()
        ImageCaptionUtils._caption_locks = {}
        ImageCaptionUtils._caption_semaphore = None

    async def test_prefetch_deduplicates_and_runs_in_parallel(self):
        await ImageCaptionUtils.prefetch_captions(["a", "a", "b", "c"])

        self.assertEqual(sorted(self.provider.calls), ["a", "b", "c"])
        self.assertGreater(self.provider.max_active, 1)

    async def test_concurrent_prefetch_reuses_inflight_caption(self):
        await asyncio.gather(
            ImageCaptionUtils.prefetch_captions(["same"]),
            ImageCaptionUtils.prefetch_captions(["same"]),
        )

        self.assertEqual(self.provider.calls, ["same"])

    async def test_prefetch_skips_when_captioning_disabled(self):
        ImageCaptionUtils.init(
            FakeContext(self.provider),
            {"image_processing": {"use_image_caption": False}},
        )

        await ImageCaptionUtils.prefetch_captions(["a"])

        self.assertEqual(self.provider.calls, [])

    def test_init_reads_caption_concurrency_from_config(self):
        ImageCaptionUtils.init(
            FakeContext(self.provider),
            {
                "image_processing": {
                    "use_image_caption": True,
                    "image_caption_concurrency": 2,
                }
            },
        )

        self.assertEqual(ImageCaptionUtils._max_concurrency, 2)

    async def test_outline_skips_caption_for_disallowed_history_image(self):
        calls = []
        original = ImageCaptionUtils.generate_image_caption

        async def fake_caption(image, umo=None, timeout=30):
            calls.append(image)
            return "cap"

        ImageCaptionUtils.generate_image_caption = staticmethod(fake_caption)
        try:
            registry = ImageRegistry({"b.jpg"})
            text = await MessageUtils.outline_message_list(
                [FakeImage("a.jpg"), FakeImage("b.jpg")],
                image_registry=registry,
            )
        finally:
            ImageCaptionUtils.generate_image_caption = staticmethod(original)

        self.assertEqual(calls, ["b.jpg"])
        self.assertIn("[图片]", text)
        self.assertIn("[图片#1：cap]", text)


if __name__ == "__main__":
    unittest.main()
