class ProviderUnavailable(RuntimeError):
    pass


async def resolve_provider(context, umo: str, provider_id: str):
    provider = context.get_provider_by_id(provider_id) if provider_id else None
    if provider is None and not provider_id:
        provider = await context.get_using_provider_async(umo)
    if provider is None or not hasattr(provider, "text_chat"):
        raise ProviderUnavailable(provider_id or "session provider")
    return provider


def provider_supports_images(provider) -> bool:
    if getattr(provider, "supports_image", False):
        return True
    provider_config = getattr(provider, "provider_config", None) or {}
    modalities = provider_config.get("modalities", [])
    return isinstance(modalities, list) and "image" in modalities
