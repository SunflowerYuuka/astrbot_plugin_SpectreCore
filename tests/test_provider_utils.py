import types
import unittest
from unittest.mock import AsyncMock

from tests._orchestration_helpers import load_utils_module


class ProviderUtilsTest(unittest.IsolatedAsyncioTestCase):
    async def test_uses_explicit_provider(self):
        mod = load_utils_module("provider_utils")
        provider = types.SimpleNamespace(text_chat=lambda **kwargs: None)
        context = type("C", (), {"get_provider_by_id": lambda self, pid: provider})()
        self.assertIs(await mod.resolve_provider(context, "umo", "fast"), provider)

    async def test_uses_session_provider_when_id_empty(self):
        mod = load_utils_module("provider_utils")
        provider = types.SimpleNamespace(text_chat=lambda **kwargs: None)
        context = type("C", (), {"get_using_provider_async": AsyncMock(return_value=provider)})()
        self.assertIs(await mod.resolve_provider(context, "umo", ""), provider)

    async def test_missing_provider_raises(self):
        mod = load_utils_module("provider_utils")
        context = type("C", (), {
            "get_provider_by_id": lambda self, pid: None,
            "get_using_provider_async": AsyncMock(return_value=None),
        })()
        with self.assertRaises(mod.ProviderUnavailable):
            await mod.resolve_provider(context, "umo", "missing")

    def test_image_capability_defaults_false(self):
        mod = load_utils_module("provider_utils")
        self.assertFalse(mod.provider_supports_images(object()))
        provider = type("P", (), {"provider_config": {"modalities": ["text", "image"]}})()
        self.assertTrue(mod.provider_supports_images(provider))
