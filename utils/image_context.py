from typing import Iterable, List, Optional, Sequence


class ImageRegistry:
    """为聊天上下文中的图片分配稳定编号，并记录需要随请求附带的图片。"""

    def __init__(self, allowed_urls: Optional[Iterable[str]] = None):
        self._allowed = None if allowed_urls is None else set(allowed_urls)
        self._urls: List[str] = []
        self._labels: dict[str, int] = {}

    def allow_all(self) -> None:
        self._allowed = None

    def is_allowed(self, url: Optional[str]) -> bool:
        if not url:
            return False
        return self._allowed is None or url in self._allowed

    def label_for(self, url: Optional[str]) -> Optional[int]:
        if not url:
            return None
        if url in self._labels:
            return self._labels[url]
        if not self.is_allowed(url):
            return None
        label = len(self._urls) + 1
        self._urls.append(url)
        self._labels[url] = label
        return label

    @property
    def urls(self) -> List[str]:
        return list(self._urls)

    @property
    def count(self) -> int:
        return len(self._urls)


def _collect_newest_first(
    image_urls_by_message: Iterable[Sequence[str]], limit: Optional[int]
) -> List[str]:
    collected: List[str] = []
    seen = set()
    for urls in reversed(list(image_urls_by_message)):
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            collected.append(url)
            if limit is not None and len(collected) >= limit:
                return collected
    return collected


def select_recent_urls(image_urls_by_message: Iterable[Sequence[str]], limit: int) -> List[str]:
    """忽略消息窗口，从全部历史中取最新的图片。"""
    if limit <= 0:
        return []
    return _collect_newest_first(image_urls_by_message, limit)


def select_context_urls(
    image_urls_by_message: Iterable[Sequence[str]], lookback: int, max_images: int
) -> List[str]:
    """只扫描最近 lookback 条消息，从新到旧最多选取 max_images 张图片。"""
    if lookback <= 0 or max_images <= 0:
        return []
    messages = list(image_urls_by_message)
    return _collect_newest_first(messages[-lookback:], max_images)


def _component_kind(component) -> str:
    raw_type = getattr(component, "type", None)
    if raw_type is not None:
        kind = getattr(raw_type, "value", raw_type)
        if kind:
            return str(kind).lower()
    return type(component).__name__.lower()


def collect_image_urls(components) -> List[str]:
    """收集消息链中的图片，包括引用消息里的图片，并按 URL 去重。"""
    urls: List[str] = []
    seen = set()

    def walk(items) -> None:
        for component in items or []:
            kind = _component_kind(component)
            if kind == "image":
                url = getattr(component, "file", None) or getattr(component, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
            elif kind == "reply":
                walk(getattr(component, "chain", None))

    walk(components)
    return urls


def build_image_instruction(image_count: int) -> str:
    if image_count <= 0:
        return ""
    return (
        f"\n\n随附了 {image_count} 张图片，聊天记录和当前消息中的 [图片#编号] "
        "按编号顺序与这些图片一一对应。请结合图片所在的消息、发送者和时间去理解图片，"
        "判断它与当前对话是否相关；只有当消息内容明确指向某张图片或表明图片之间有关联时，"
        "才把它们联系起来，不要仅因为多张图片同时出现就认为它们相关。"
    )
