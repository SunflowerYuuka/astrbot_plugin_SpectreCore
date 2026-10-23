def matches_ignored_prefix(text: str, config) -> bool:
    frequency = config.get("model_frequency", {}) or {}
    prefixes = frequency.get("ignored_prefixes", []) or []
    message = (text or "").lstrip()
    return any(
        isinstance(prefix, str) and bool(prefix.strip()) and message.startswith(prefix.strip())
        for prefix in prefixes
    )
