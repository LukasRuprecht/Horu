"""AI providers. Horu never ships keys: users bring their own, or run a local model."""

from __future__ import annotations

from horu.llm.base import AIError, Chat, Provider


def create(kind: str, model: str, base_url: str = "", api_key: str = "") -> Provider:
    """Build a provider. `kind` is "anthropic" or "openai_compat"."""
    if not model:
        raise AIError("Choose a model in Horu's AI settings.")
    if kind == "anthropic":
        from horu.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model, api_key)
    if kind == "openai_compat":
        from horu.llm.openai_compat import OpenAICompatProvider

        if not base_url:
            raise AIError("This provider needs a server address (base URL).")
        return OpenAICompatProvider(model, base_url, api_key)
    raise AIError(f"Unknown AI provider type {kind!r}.")


__all__ = ["AIError", "Chat", "Provider", "create"]
