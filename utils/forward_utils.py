from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, List, Optional


FetchForward = Callable[[str], Awaitable[Any]]
CaptionFunc = Callable[[str], Awaitable[Optional[str]]]


@dataclass
class ForwardSegment:
    kind: str
    text: str = ""
    url: str = ""
    name: str = ""
    user_id: str = ""
    forward_id: str = ""
    nested_nodes: List["ForwardNode"] = field(default_factory=list)


@dataclass
class ForwardNode:
    name: str
    user_id: str = ""
    timestamp: Optional[int] = None
    segments: List[ForwardSegment] = field(default_factory=list)


def _component_kind(component: Any) -> str:
    raw_type = getattr(component, "type", None)
    if raw_type is not None:
        kind = getattr(raw_type, "value", raw_type)
        if kind:
            return str(kind).lower()
    return type(component).__name__.lower()


def _component_id(component: Any) -> str:
    for attr in ("id", "message_id", "forward_id", "resid"):
        value = getattr(component, attr, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _as_int(value: Any) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _format_timestamp(value: Any) -> str:
    timestamp = _as_int(value)
    if timestamp is None:
        return ""
    try:
        moment = datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return ""
    if moment.date() == datetime.now().date():
        return moment.strftime("%H:%M")
    return moment.strftime("%m-%d %H:%M")


def _json_summary(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return "[JSON消息]"
    if not isinstance(value, dict):
        return "[JSON消息]"
    if value.get("prompt"):
        return f"[JSON卡片:{value['prompt']}]"
    if value.get("app"):
        return f"[小程序:{value['app']}]"
    return "[JSON消息]"


class ForwardUtils:
    """将 QQ 合并转发消息展开为适合大模型阅读的文本。"""

    def __init__(
        self,
        fetch_forward: Optional[FetchForward] = None,
        max_depth: int = 2,
        max_nodes: int = 50,
        max_chars: int = 2000,
        fetch_timeout: float = 5.0,
    ):
        self._fetch_forward = fetch_forward
        self.max_depth = max(0, int(max_depth))
        self.max_nodes = max(0, int(max_nodes))
        self.max_chars = max(0, int(max_chars))
        self.fetch_timeout = max(0.1, float(fetch_timeout))
        self._cache: dict[str, List[ForwardNode]] = {}

    async def render(
        self,
        component: Any,
        *,
        image_registry: Any = None,
        caption_func: Optional[CaptionFunc] = None,
    ) -> Optional[str]:
        nodes = await self.expand(component)
        if nodes is None:
            return None

        seen: set[str] = set()
        if _component_kind(component) in ("forward", "forward_msg"):
            forward_id = _component_id(component)
            if forward_id:
                seen.add(forward_id)

        return await self._render_nodes(
            nodes,
            depth=0,
            seen=seen,
            image_registry=image_registry,
            caption_func=caption_func,
        )

    async def expand(
        self,
        component: Any,
        *,
        depth: int = 0,
        seen: Optional[set[str]] = None,
    ) -> Optional[List[ForwardNode]]:
        kind = _component_kind(component)
        if kind == "nodes":
            return [
                self._node_from_component(node)
                for node in (getattr(component, "nodes", None) or [])
            ]
        if kind == "node":
            return [self._node_from_component(component)]
        if kind in ("forward", "forward_msg"):
            if depth >= self.max_depth:
                return []
            forward_id = _component_id(component)
            if not forward_id:
                return []
            return await self._load_forward(forward_id, seen=seen)
        return None

    async def _load_forward(
        self,
        forward_id: str,
        *,
        seen: Optional[set[str]] = None,
    ) -> List[ForwardNode]:
        if seen is None:
            seen = set()
        if forward_id in seen:
            return []
        if forward_id in self._cache:
            return self._cache[forward_id]
        if self._fetch_forward is None:
            return []

        seen.add(forward_id)
        try:
            payload = await asyncio.wait_for(
                self._fetch_forward(forward_id),
                timeout=self.fetch_timeout,
            )
        except Exception:
            return []

        nodes = self._parse_payload(payload)
        self._cache[forward_id] = nodes
        return nodes

    def _node_from_component(self, node: Any) -> ForwardNode:
        name = (
            getattr(node, "name", None)
            or getattr(node, "uin", None)
            or "未知用户"
        )
        user_id = str(getattr(node, "uin", None) or "")
        timestamp = _as_int(getattr(node, "time", None))
        segments = [
            self._segment_from_component(component)
            for component in (getattr(node, "content", None) or [])
        ]
        return ForwardNode(
            name=str(name),
            user_id=user_id,
            timestamp=timestamp,
            segments=segments,
        )

    def _segment_from_component(self, component: Any) -> ForwardSegment:
        kind = _component_kind(component)
        if kind in ("plain", "text"):
            return ForwardSegment(kind="text", text=str(getattr(component, "text", "") or ""))
        if kind == "image":
            url = getattr(component, "file", None) or getattr(component, "url", None)
            return ForwardSegment(kind="image", url=str(url or ""))
        if kind == "at":
            qq = getattr(component, "qq", None)
            name = getattr(component, "name", None)
            if str(qq).lower() == "all":
                return ForwardSegment(kind="text", text="@全体成员")
            if name:
                return ForwardSegment(kind="text", text=f"@{name}({qq})")
            return ForwardSegment(kind="text", text=f"@{qq}")
        if kind == "face":
            return ForwardSegment(kind="text", text=f"[表情:{getattr(component, 'id', '')}]")
        if kind == "record":
            return ForwardSegment(kind="text", text="[语音]")
        if kind == "video":
            return ForwardSegment(kind="text", text="[视频]")
        if kind == "file":
            return ForwardSegment(kind="text", text=f"[文件:{getattr(component, 'name', '') or 'file'}]")
        if kind in ("forward", "forward_msg"):
            return ForwardSegment(kind="forward", forward_id=_component_id(component))
        if kind == "node":
            return ForwardSegment(
                kind="nested",
                nested_nodes=[self._node_from_component(component)],
            )
        if kind == "nodes":
            return ForwardSegment(
                kind="nested",
                nested_nodes=[
                    self._node_from_component(node)
                    for node in (getattr(component, "nodes", None) or [])
                ],
            )
        if kind == "json":
            return ForwardSegment(kind="text", text=_json_summary(getattr(component, "data", None)))
        if kind == "share":
            title = getattr(component, "title", "") or ""
            content = getattr(component, "content", "") or ""
            return ForwardSegment(kind="text", text=f"[分享:《{title}》{content}]")
        if kind == "location":
            title = getattr(component, "title", "") or ""
            return ForwardSegment(kind="text", text=f"[位置:{title}]")
        if kind == "music":
            title = getattr(component, "title", "") or ""
            return ForwardSegment(kind="text", text=f"[音乐:{title}]")
        if kind == "contact":
            return ForwardSegment(kind="text", text=f"[联系人:{getattr(component, 'id', '')}]")
        if kind == "poke":
            return ForwardSegment(kind="text", text=f"[戳一戳 对:{getattr(component, 'qq', '')}]")
        if kind == "reply":
            return ForwardSegment(kind="text", text="[引用消息]")
        return ForwardSegment(kind="text", text=f"[{kind}]")

    async def _prefetch_image_captions(
        self,
        nodes: List[ForwardNode],
        *,
        image_registry: Any = None,
        caption_func: Optional[CaptionFunc] = None,
    ) -> dict[str, Optional[str]]:
        if caption_func is None:
            return {}

        sources: dict[str, str] = {}
        for node in nodes:
            for segment in node.segments:
                if segment.kind != "image" or not segment.url:
                    continue
                if segment.url in sources:
                    continue
                if image_registry is not None and not image_registry.is_allowed(
                    segment.url
                ):
                    continue

                source = segment.url
                if source.startswith("file:///"):
                    path = source[8:]
                    if not os.path.exists(path):
                        continue
                    source = path
                sources[segment.url] = source

        if not sources:
            return {}

        urls = list(sources)
        results = await asyncio.gather(
            *(caption_func(sources[url]) for url in urls),
            return_exceptions=True,
        )
        return {
            url: result if isinstance(result, str) else None
            for url, result in zip(urls, results)
        }

    async def _render_nodes(
        self,
        nodes: List[ForwardNode],
        *,
        depth: int,
        seen: set[str],
        image_registry: Any = None,
        caption_func: Optional[CaptionFunc] = None,
    ) -> str:
        if not nodes:
            return "[合并转发消息]"

        limit = self.max_nodes if self.max_nodes > 0 else 0
        shown_nodes = nodes[:limit] if limit else nodes
        captions = await self._prefetch_image_captions(
            shown_nodes,
            image_registry=image_registry,
            caption_func=caption_func,
        )
        lines = ["[合并转发消息]"]
        used = len(lines[0])

        for node in shown_nodes:
            content = await self._render_segments(
                node.segments,
                depth=depth,
                seen=seen,
                image_registry=image_registry,
                caption_func=caption_func,
                captions=captions,
            )
            if not content:
                content = "[空消息]"

            prefix = node.name or node.user_id or "未知用户"
            timestamp = _format_timestamp(node.timestamp)
            if timestamp:
                prefix = f"{timestamp} {prefix}"
            line = f"{prefix}: {content}"

            if self.max_chars and used + len(line) + 1 > self.max_chars:
                available = self.max_chars - used - 1
                if available > 20:
                    lines.append(line[:available] + "...")
                lines.append("...(合并转发内容过长，已截断)")
                return "\n".join(lines)

            lines.append(line)
            used += len(line) + 1

        remaining = len(nodes) - len(shown_nodes)
        if remaining > 0:
            lines.append(f"...(还有 {remaining} 条消息未显示)")
        return "\n".join(lines)

    async def _render_segments(
        self,
        segments: List[ForwardSegment],
        *,
        depth: int,
        seen: set[str],
        image_registry: Any = None,
        caption_func: Optional[CaptionFunc] = None,
        captions: Optional[dict[str, Optional[str]]] = None,
    ) -> str:
        parts: List[str] = []
        for segment in segments:
            if segment.kind == "text":
                parts.append(segment.text)
            elif segment.kind == "image":
                parts.append(
                    await self._render_image(
                        segment.url,
                        image_registry=image_registry,
                        caption_func=caption_func,
                        captions=captions,
                    )
                )
            elif segment.kind == "forward":
                if (
                    not segment.forward_id
                    or depth + 1 >= self.max_depth
                    or segment.forward_id in seen
                ):
                    parts.append("[合并转发消息]")
                    continue
                seen.add(segment.forward_id)
                try:
                    nodes = await self._load_forward(segment.forward_id)
                    parts.append(
                        await self._render_nodes(
                            nodes,
                            depth=depth + 1,
                            seen=seen,
                            image_registry=image_registry,
                            caption_func=caption_func,
                        )
                    )
                finally:
                    seen.discard(segment.forward_id)
            elif segment.kind == "nested":
                if depth + 1 >= self.max_depth:
                    parts.append("[合并转发消息]")
                    continue
                parts.append(
                    await self._render_nodes(
                        segment.nested_nodes,
                        depth=depth + 1,
                        seen=seen,
                        image_registry=image_registry,
                        caption_func=caption_func,
                    )
                )
        return "".join(parts)

    async def _render_image(
        self,
        url: str,
        *,
        image_registry: Any = None,
        caption_func: Optional[CaptionFunc] = None,
        captions: Optional[dict[str, Optional[str]]] = None,
    ) -> str:
        label = image_registry.label_for(url) if image_registry is not None else None
        if image_registry is not None and label is None:
            return "[图片]"

        caption = None
        if captions is not None and url in captions:
            caption = captions[url]
        elif caption_func is not None:
            caption_source = url
            if url.startswith("file:///"):
                path = url[8:]
                if os.path.exists(path):
                    caption_source = path
            try:
                caption = await caption_func(caption_source)
            except Exception:
                caption = None

        if label is not None:
            suffix = f"：{caption}" if caption else ""
            return f"[图片#{label}{suffix}]"
        if caption:
            return f"[图片：{caption}]"
        return "[图片]"

    def _parse_payload(self, payload: Any) -> List[ForwardNode]:
        data = payload
        if isinstance(payload, dict):
            wrapped = payload.get("data")
            if isinstance(wrapped, (dict, list)):
                data = wrapped

        if isinstance(data, list):
            raw_nodes = data
        elif isinstance(data, dict):
            raw_nodes = (
                data.get("messages")
                or data.get("message")
                or data.get("nodes")
                or data.get("nodeList")
            )
            if not isinstance(raw_nodes, list):
                raw_nodes = [data] if ("sender" in data or "content" in data) else []
        else:
            return []

        parsed: List[ForwardNode] = []
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                continue
            if raw.get("type") == "node" and isinstance(raw.get("data"), dict):
                raw = raw["data"]

            sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
            name = (
                sender.get("card")
                or sender.get("nickname")
                or sender.get("user_id")
                or raw.get("nickname")
                or raw.get("name")
                or raw.get("uin")
                or "未知用户"
            )
            user_id = (
                sender.get("user_id")
                or raw.get("user_id")
                or raw.get("uin")
                or ""
            )
            timestamp = _as_int(raw.get("time") or raw.get("timestamp"))
            content = raw.get("message")
            if content is None:
                content = raw.get("content")
            parsed.append(
                ForwardNode(
                    name=str(name),
                    user_id=str(user_id),
                    timestamp=timestamp,
                    segments=self._parse_segments(content),
                )
            )
        return parsed

    def _parse_segments(self, content: Any) -> List[ForwardSegment]:
        if isinstance(content, str):
            text = content.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                return [ForwardSegment(kind="text", text=text)]
            if isinstance(parsed, list):
                return self._parse_segments(parsed)
            return [ForwardSegment(kind="text", text=text)]

        if isinstance(content, dict):
            content = [content]
        if not isinstance(content, list):
            return []

        segments: List[ForwardSegment] = []
        for raw in content:
            if not isinstance(raw, dict):
                continue
            segment_type = str(raw.get("type") or "").lower()
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

            if segment_type in ("text", "plain"):
                text = data.get("text")
                if isinstance(text, str) and text:
                    segments.append(ForwardSegment(kind="text", text=text))
            elif segment_type == "image":
                url = data.get("url") or data.get("file") or data.get("path") or ""
                segments.append(ForwardSegment(kind="image", url=str(url)))
            elif segment_type == "at":
                qq = data.get("qq") or ""
                name = data.get("name") or ""
                if str(qq).lower() == "all":
                    segments.append(ForwardSegment(kind="text", text="@全体成员"))
                elif name:
                    segments.append(ForwardSegment(kind="text", text=f"@{name}({qq})"))
                else:
                    segments.append(ForwardSegment(kind="text", text=f"@{qq}"))
            elif segment_type == "face":
                segments.append(
                    ForwardSegment(kind="text", text=f"[表情:{data.get('id', '')}]")
                )
            elif segment_type == "record":
                segments.append(ForwardSegment(kind="text", text="[语音]"))
            elif segment_type == "video":
                segments.append(ForwardSegment(kind="text", text="[视频]"))
            elif segment_type == "file":
                file_name = (
                    data.get("name")
                    or data.get("file_name")
                    or data.get("file")
                    or "file"
                )
                segments.append(
                    ForwardSegment(kind="text", text=f"[文件:{file_name}]")
                )
            elif segment_type in ("forward", "forward_msg"):
                forward_id = (
                    data.get("id")
                    or data.get("message_id")
                    or data.get("resid")
                    or data.get("forward_id")
                    or ""
                )
                segments.append(
                    ForwardSegment(kind="forward", forward_id=str(forward_id))
                )
            elif segment_type == "json":
                segments.append(
                    ForwardSegment(kind="text", text=_json_summary(data.get("data", data)))
                )
            elif segment_type == "share":
                title = data.get("title") or data.get("url") or ""
                segments.append(
                    ForwardSegment(kind="text", text=f"[分享:《{title}》]")
                )
            elif segment_type == "location":
                title = data.get("title") or ""
                segments.append(
                    ForwardSegment(kind="text", text=f"[位置:{title}]")
                )
            elif segment_type == "music":
                title = data.get("title") or ""
                segments.append(
                    ForwardSegment(kind="text", text=f"[音乐:{title}]")
                )
            elif segment_type == "contact":
                segments.append(
                    ForwardSegment(kind="text", text=f"[联系人:{data.get('id', '')}]")
                )
            elif segment_type == "poke":
                segments.append(ForwardSegment(kind="text", text="[戳一戳]"))
            elif segment_type in ("node", "nodes"):
                nested = self._parse_payload(data)
                if nested:
                    segments.append(ForwardSegment(kind="nested", nested_nodes=nested))
            elif segment_type:
                segments.append(ForwardSegment(kind="text", text=f"[{segment_type}]"))

        return segments


async def fetch_forward_via_event(
    event: Any,
    forward_id: str,
) -> Optional[Any]:
    """通过 OneBot 协议的 get_forward_msg 拉取合并转发内容。"""
    bot = getattr(event, "bot", None)
    api = getattr(bot, "api", None)
    call_action = getattr(api, "call_action", None)
    if not callable(call_action):
        call_action = getattr(bot, "call_action", None)
    if not callable(call_action):
        return None

    forward_id_str = str(forward_id).strip()
    if not forward_id_str:
        return None

    params_list: List[dict[str, Any]] = [
        {"message_id": forward_id_str},
        {"id": forward_id_str},
        {"forward_id": forward_id_str},
    ]
    if forward_id_str.isdigit():
        int_id = int(forward_id_str)
        params_list.extend(
            [
                {"message_id": int_id},
                {"id": int_id},
                {"forward_id": int_id},
            ]
        )

    for params in params_list:
        for kwargs in (params, {"action": "get_forward_msg", **params}):
            try:
                if "action" in kwargs:
                    result = call_action(**kwargs)
                else:
                    result = call_action("get_forward_msg", **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, dict):
                    return result
            except TypeError:
                continue
            except Exception:
                continue
    return None
