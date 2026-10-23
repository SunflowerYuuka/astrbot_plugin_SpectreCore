import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "utils" / "image_context.py"
_spec = importlib.util.spec_from_file_location("image_context_under_test", MODULE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载被测模块: {MODULE_PATH}")
image_context = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(image_context)

ImageRegistry = image_context.ImageRegistry
build_image_instruction = image_context.build_image_instruction
collect_image_urls = image_context.collect_image_urls
select_context_urls = image_context.select_context_urls
select_recent_urls = image_context.select_recent_urls


class FakeComponent:
    def __init__(self, kind, **kwargs):
        self.type = kind
        for key, value in kwargs.items():
            setattr(self, key, value)


def image(url):
    return FakeComponent("image", file=url, url="")


def reply(chain):
    return FakeComponent("reply", chain=chain)


class ImageRegistryTests(unittest.TestCase):
    def test_assigns_labels_in_order_and_reuses_duplicates(self):
        registry = ImageRegistry()

        self.assertEqual(registry.label_for("a.jpg"), 1)
        self.assertEqual(registry.label_for("b.jpg"), 2)
        self.assertEqual(registry.label_for("a.jpg"), 1)
        self.assertEqual(registry.urls, ["a.jpg", "b.jpg"])

    def test_allowed_urls_filters_then_allow_all_opens(self):
        registry = ImageRegistry(allowed_urls={"b.jpg"})

        self.assertIsNone(registry.label_for("a.jpg"))
        self.assertEqual(registry.label_for("b.jpg"), 1)
        self.assertEqual(registry.urls, ["b.jpg"])

        registry.allow_all()

        self.assertEqual(registry.label_for("a.jpg"), 2)
        self.assertEqual(registry.urls, ["b.jpg", "a.jpg"])

    def test_empty_url_is_ignored(self):
        registry = ImageRegistry()

        self.assertIsNone(registry.label_for(""))
        self.assertIsNone(registry.label_for(None))
        self.assertEqual(registry.urls, [])

    def test_is_allowed_checks_without_assigning_labels(self):
        registry = ImageRegistry({"b.jpg"})

        self.assertFalse(registry.is_allowed("a.jpg"))
        self.assertTrue(registry.is_allowed("b.jpg"))
        self.assertEqual(registry.urls, [])

        registry.label_for("b.jpg")
        self.assertEqual(registry.urls, ["b.jpg"])


class ImageSelectionTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            ["old.jpg"],
            ["middle.jpg"],
            ["newest-1.jpg", "newest-2.jpg"],
        ]

    def test_context_selection_uses_only_lookback_and_newest_first(self):
        selected = select_context_urls(self.messages, lookback=2, max_images=10)

        self.assertEqual(selected, ["newest-1.jpg", "newest-2.jpg", "middle.jpg"])

    def test_context_selection_respects_image_cap(self):
        selected = select_context_urls(self.messages, lookback=10, max_images=2)

        self.assertEqual(selected, ["newest-1.jpg", "newest-2.jpg"])

    def test_context_selection_can_be_disabled(self):
        self.assertEqual(select_context_urls(self.messages, lookback=0, max_images=5), [])
        self.assertEqual(select_context_urls(self.messages, lookback=5, max_images=0), [])

    def test_recent_selection_scans_all_history_newest_first(self):
        selected = select_recent_urls(self.messages, limit=2)

        self.assertEqual(selected, ["newest-1.jpg", "newest-2.jpg"])

    def test_recent_selection_deduplicates_urls(self):
        selected = select_recent_urls([["a.jpg"], ["a.jpg", "b.jpg"]], limit=10)

        self.assertEqual(selected, ["a.jpg", "b.jpg"])


class ImageCollectionTests(unittest.TestCase):
    def test_collects_top_level_and_reply_chain_images_without_duplicates(self):
        chain = [
            image("a.jpg"),
            reply([image("b.jpg"), image("a.jpg")]),
            FakeComponent("plain", text="hi"),
        ]

        self.assertEqual(collect_image_urls(chain), ["a.jpg", "b.jpg"])

    def test_collects_url_when_file_is_empty(self):
        chain = [FakeComponent("image", file="", url="https://example.com/a.jpg")]

        self.assertEqual(collect_image_urls(chain), ["https://example.com/a.jpg"])

    def test_ignores_reply_without_chain(self):
        self.assertEqual(collect_image_urls([FakeComponent("reply", chain=None)]), [])


class ImageInstructionTests(unittest.TestCase):
    def test_no_instruction_without_images(self):
        self.assertEqual(build_image_instruction(0), "")

    def test_instruction_contains_mapping_and_relevance_rule(self):
        instruction = build_image_instruction(2)

        self.assertIn("2 张", instruction)
        self.assertIn("[图片#编号]", instruction)
        self.assertIn("编号顺序", instruction)
        self.assertIn("不要仅因为多张图片同时出现就认为它们相关", instruction)


if __name__ == "__main__":
    unittest.main()
