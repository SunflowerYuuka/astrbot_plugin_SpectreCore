import importlib.util
import pathlib
import sys
import unittest
from datetime import datetime


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "utils" / "forward_utils.py"
_spec = importlib.util.spec_from_file_location("forward_utils_under_test", MODULE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载被测模块: {MODULE_PATH}")
forward_utils = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = forward_utils
_spec.loader.exec_module(forward_utils)

IMAGE_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "utils" / "image_context.py"
_image_spec = importlib.util.spec_from_file_location(
    "image_context_for_forward_test", IMAGE_MODULE_PATH
)
if _image_spec is None or _image_spec.loader is None:
    raise ImportError(f"无法加载图片模块: {IMAGE_MODULE_PATH}")
image_context = importlib.util.module_from_spec(_image_spec)
_image_spec.loader.exec_module(image_context)

ForwardUtils = forward_utils.ForwardUtils
fetch_forward_via_event = forward_utils.fetch_forward_via_event
ImageRegistry = image_context.ImageRegistry


class FakeComponent:
    def __init__(self, kind, **kwargs):
        self.type = kind
        for key, value in kwargs.items():
            setattr(self, key, value)


def plain(text):
    return FakeComponent("plain", text=text)


def image(url):
    return FakeComponent("image", file=url, url="")


def node(name, content, uin="", timestamp=None):
    return FakeComponent(
        "node",
        name=name,
        uin=uin,
        time=timestamp,
        content=content,
    )


def nodes(*items):
    return FakeComponent("nodes", nodes=list(items))


def forward(forward_id):
    return FakeComponent("forward", id=forward_id)


def text_node(name, text, user_id="", timestamp=None):
    return node(name, [plain(text)], uin=user_id, timestamp=timestamp)


def forward_payload(*messages):
    return {"status": "ok", "retcode": 0, "data": {"messages": list(messages)}}


def payload_message(sender_name, text, user_id="", extra_segments=None):
    segments = [{"type": "text", "data": {"text": text}}]
    if extra_segments:
        segments.extend(extra_segments)
    return {
        "sender": {"nickname": sender_name, "user_id": user_id},
        "message": segments,
    }


class ForwardRenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_renders_nodes_with_sender_and_plain_text(self):
        component = nodes(
            text_node("小明", "你好", "10001"),
            text_node("小红", "在吗", "10002"),
        )

        rendered = await ForwardUtils().render(component)

        self.assertIsNotNone(rendered)
        self.assertIn("[合并转发消息]", rendered)
        self.assertIn("小明: 你好", rendered)
        self.assertIn("小红: 在吗", rendered)

    async def test_renders_single_node_and_timestamp(self):
        timestamp = int(datetime(2026, 10, 23, 12, 34).timestamp())
        component = node(
            "小明",
            [plain("午安")],
            uin="10001",
            timestamp=timestamp,
        )

        rendered = await ForwardUtils().render(component)

        self.assertIsNotNone(rendered)
        self.assertIn("12:34 小明: 午安", rendered)

    async def test_non_forward_component_returns_none(self):
        self.assertIsNone(await ForwardUtils().render(plain("hello")))

    async def test_fetches_forward_payload_and_caches_it(self):
        calls = []

        async def fetch(forward_id):
            calls.append(forward_id)
            return forward_payload(payload_message("小明", "hello", "10001"))

        parser = ForwardUtils(fetch_forward=fetch)

        first = await parser.render(forward("f1"))
        second = await parser.render(forward("f1"))

        self.assertIsNotNone(first)
        self.assertIn("小明: hello", first)
        self.assertIsNotNone(second)
        self.assertEqual(calls, ["f1"])

    async def test_fetch_failure_falls_back_to_placeholder(self):
        async def fetch(_forward_id):
            raise RuntimeError("boom")

        rendered = await ForwardUtils(fetch_forward=fetch).render(forward("f1"))

        self.assertEqual(rendered, "[合并转发消息]")

    async def test_nested_forward_is_expanded(self):
        async def fetch(forward_id):
            if forward_id == "outer":
                return forward_payload(
                    payload_message(
                        "小明",
                        "外层",
                        "10001",
                        [
                            {
                                "type": "forward",
                                "data": {"id": "inner"},
                            }
                        ],
                    )
                )
            return forward_payload(payload_message("小红", "内层", "10002"))

        rendered = await ForwardUtils(
            fetch_forward=fetch,
            max_depth=2,
        ).render(forward("outer"))

        self.assertIsNotNone(rendered)
        self.assertIn("小明: 外层[合并转发消息]", rendered)
        self.assertIn("小红: 内层", rendered)

    async def test_max_depth_stops_nested_fetch(self):
        calls = []

        async def fetch(forward_id):
            calls.append(forward_id)
            return forward_payload(
                payload_message(
                    "小明",
                    "外层",
                    "10001",
                    [{"type": "forward", "data": {"id": "inner"}}],
                )
            )

        rendered = await ForwardUtils(
            fetch_forward=fetch,
            max_depth=1,
        ).render(forward("outer"))

        self.assertIsNotNone(rendered)
        self.assertEqual(calls, ["outer"])
        self.assertIn("小明: 外层[合并转发消息]", rendered)

    async def test_images_use_registry_label_and_caption(self):
        async def caption(_url):
            return "一只猫"

        registry = ImageRegistry()
        component = nodes(
            node("小明", [plain("看这个"), image("cat.jpg")], uin="10001"),
        )

        rendered = await ForwardUtils().render(
            component,
            image_registry=registry,
            caption_func=caption,
        )

        self.assertIsNotNone(rendered)
        self.assertIn("[图片#1：一只猫]", rendered)
        self.assertEqual(registry.urls, ["cat.jpg"])

    async def test_max_nodes_marks_remaining_messages(self):
        component = nodes(
            text_node("A", "第一条"),
            text_node("B", "第二条"),
            text_node("C", "第三条"),
        )

        rendered = await ForwardUtils(max_nodes=2).render(component)

        self.assertIsNotNone(rendered)
        self.assertIn("第一条", rendered)
        self.assertIn("第二条", rendered)
        self.assertNotIn("第三条", rendered)
        self.assertIn("还有 1 条消息未显示", rendered)

    async def test_max_chars_truncates_long_content(self):
        component = nodes(text_node("A", "很长的内容" * 100))

        rendered = await ForwardUtils(max_chars=80).render(component)

        self.assertIsNotNone(rendered)
        self.assertIn("已截断", rendered)


class FetchForwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_aiocqhttp_call_action(self):
        calls = []

        class FakeApi:
            async def call_action(self, action, **params):
                calls.append((action, params))
                return {"data": {"messages": []}}

        class FakeBot:
            api = FakeApi()

        class FakeEvent:
            bot = FakeBot()

        result = await fetch_forward_via_event(FakeEvent(), "123")

        self.assertEqual(result, {"data": {"messages": []}})
        self.assertEqual(calls[0][0], "get_forward_msg")
        self.assertEqual(calls[0][1], {"message_id": "123"})


if __name__ == "__main__":
    unittest.main()
