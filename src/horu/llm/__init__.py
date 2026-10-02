"""AI providers. Horu never ships keys: users bring their own, or run a local model."""

from __future__ import annotations

from horu.llm.base import AIError, Chat, Provider


def create(provider: str, model: str, base_url: str = "", api_key_env: str = "") -> Provider | None:
    """Build the configured provider, or return None when AI is turned off."""
    if provider == "none":
        return None
    if provider == "anthropic":
        from horu.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model)
    if provider == "openai_compat":
        from horu.llm.openai_compat import OpenAICompatProvider

        if not base_url:
            raise AIError('provider "openai_compat" needs base_url in the [ai] section of config.toml.')
        return OpenAICompatProvider(model, base_url, api_key_env)
    raise AIError(f"Unknown AI provider {provider!r} in config.toml.")


__all__ = ["AIError", "Chat", "Provider", "create"]
