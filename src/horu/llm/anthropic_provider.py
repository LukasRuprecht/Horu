"""Claude through the official Anthropic SDK."""

from __future__ import annotations

from typing import Iterator

from horu.llm.base import AIError

# Models that accept server-side refusal fallbacks (`fallbacks: "default"`): if the model's
# safety classifiers decline a request, the API re-runs it on a suitable model instead of
# returning a refusal.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that reject the `effort` setting.
_NO_EFFORT_PREFIXES = ("claude-haiku",)
_MAX_TOKENS = 16000  # includes thinking; explanations themselves are short


class AnthropicProvider:
    def __init__(self, model: str, client=None) -> None:
        self.model = model
        self.label = f"Claude ({model})"
        if client is None:
            import anthropic

            # Credentials come from the environment (ANTHROPIC_API_KEY, e.g. via .env). A
            # missing key only surfaces when the first request is made.
            client = anthropic.Anthropic()
        self._client = client

    def chat(self, system: str, effort: str = "low") -> AnthropicChat:
        return AnthropicChat(self, system, effort)


class AnthropicChat:
    def __init__(self, provider: AnthropicProvider, system: str, effort: str) -> None:
        self._provider = provider
        self._system = system
        self._effort = effort
        self._messages: list[dict] = []
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def send(self, text: str) -> Iterator[str]:
        import anthropic

        model = self._provider.model
        user_message = {"role": "user", "content": text}
        request: dict = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            # Cached so follow-up questions in the same chat reuse the prefix.
            "system": [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            "messages": [*self._messages, user_message],
        }
        # Thinking is left at the model's default (adaptive where supported); effort is the
        # control. Low effort keeps replies quick, which matters mid-game.
        if not model.startswith(_NO_EFFORT_PREFIXES):
            request["output_config"] = {"effort": self._effort}

        self._cancelled = False
        try:
            if model in _FALLBACK_MODELS:
                manager = self._provider._client.beta.messages.stream(
                    **request, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                manager = self._provider._client.messages.stream(**request)
            with manager as stream:
                for chunk in stream.text_stream:
                    if self._cancelled:
                        return  # leaving the context manager closes the connection
                    yield chunk
                final = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AIError("Claude rejected the API key. Check ANTHROPIC_API_KEY in your .env file.") from e
        except anthropic.PermissionDeniedError as e:
            raise AIError(f"This API key can't use {model}.") from e
        except anthropic.NotFoundError as e:
            raise AIError(f"Unknown Claude model: {model}. Check [ai] model in config.toml.") from e
        except anthropic.RateLimitError as e:
            raise AIError("Claude's rate limit was hit. Wait a moment and try again.") from e
        except anthropic.APIStatusError as e:
            raise AIError(f"Claude returned an error ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIError("Couldn't reach Claude. Check your internet connection.") from e
        except TypeError as e:
            # The SDK raises TypeError when it finds no credentials at all.
            if "authentication" not in str(e):
                raise
            raise AIError("No Claude API key found. Put ANTHROPIC_API_KEY in your .env file.") from e

        if final.stop_reason == "refusal":
            raise AIError("Claude declined to answer this one.")
        # Append-only history: the full response content (thinking blocks included) goes
        # back unchanged on the next turn.
        self._messages += [user_message, {"role": "assistant", "content": final.content}]
