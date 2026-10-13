"""Claude through the official Anthropic SDK."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from horu.llm.base import AIError
from horu.llm.tools import Tool

# Models that accept server-side refusal fallbacks (`fallbacks: "default"`): if the model's
# safety classifiers decline a request, the API re-runs it on a suitable model instead of
# returning a refusal.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that reject the `effort` setting.
_NO_EFFORT_PREFIXES = ("claude-haiku",)
_MAX_TOKENS = 16000  # includes thinking; explanations themselves are short


class AnthropicProvider:
    def __init__(self, model: str, api_key: str = "", client=None) -> None:
        self.model = model
        self.label = f"Claude ({model})"
        if client is None:
            import anthropic

            # Without an explicit key the SDK reads the environment (ANTHROPIC_API_KEY). A
            # missing key only surfaces when the first request is made.
            # No automatic retries: each is another request, and a failed line shows Retry.
            options = {"max_retries": 0, "timeout": anthropic.Timeout(60, connect=10)}
            client = anthropic.Anthropic(api_key=api_key, **options) if api_key else anthropic.Anthropic(**options)
        self._client = client

    def chat(
        self, system: str, effort: str = "low", history: list[dict] | None = None, temperature: float | None = None
    ) -> AnthropicChat:
        # `temperature` is ignored: current Claude models reject sampling parameters.
        return AnthropicChat(self, system, effort, history)

    def read_image(self, prompt: str, png: bytes, effort: str = "low") -> str:
        request = self._request("", effort)
        messages = [{"role": "user", "content": _with_image(prompt, png)}]
        with _errors(self.model):
            if self.model in _FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    **request, messages=messages, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.create(**request, messages=messages)
        if response.stop_reason == "refusal":
            raise AIError("Claude declined to read this one.")
        return "".join(block.text for block in response.content if block.type == "text").strip()

    def run_tools(
        self, system: str, task: str, tools: list[Tool], effort: str = "low", max_turns: int = 12, follow_up=None
    ) -> str:
        """Let Claude work on `task`, calling `tools` as it decides, until it answers.

        A manual tool-use loop rather than the SDK's tool runner, so the same agent code
        works with every provider Horu supports. All of a turn's tool results go back in one
        message, and each response is appended unchanged (append-only history).
        """
        by_name = {tool.name: tool for tool in tools}
        request = self._request(system, effort)
        # Strict schemas guarantee tool inputs match; Opus 5.5 doesn't allow forcing a tool
        # call, so the task prompt asks for them instead.
        request["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters, "strict": True} for t in tools
        ]
        messages: list[dict] = [{"role": "user", "content": task}]
        for _ in range(max_turns):
            with _errors(self.model):
                if self.model in _FALLBACK_MODELS:
                    response = self._client.beta.messages.create(
                        **request, messages=messages, betas=[_FALLBACK_BETA], fallbacks="default"
                    )
                else:
                    response = self._client.messages.create(**request, messages=messages)
            if response.stop_reason == "refusal":
                raise AIError("Claude declined to do this one.")
            messages.append({"role": "assistant", "content": response.content})
            calls = [block for block in response.content if block.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                answer = "".join(block.text for block in response.content if block.type == "text").strip()
                nudge = follow_up(answer) if follow_up else None
                if nudge is None:
                    return answer
                messages.append({"role": "user", "content": nudge})
                continue
            results = []
            for call in calls:
                tool = by_name.get(call.name)
                result, is_error = tool.call(dict(call.input)) if tool else (f"Error: no tool named {call.name}", True)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": result, **({"is_error": True} if is_error else {})})
            messages.append({"role": "user", "content": results})
        raise AIError("That took more steps than expected, so Horu stopped it.")

    def _request(self, system: str, effort: str) -> dict:
        request: dict = {"model": self.model, "max_tokens": _MAX_TOKENS}
        if system:
            # Cached so follow-up turns reuse the prefix.
            request["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        # Thinking is left at the model's default (adaptive where supported); effort is the
        # control. Low effort keeps replies quick, which matters mid-game.
        if not self.model.startswith(_NO_EFFORT_PREFIXES):
            request["output_config"] = {"effort": "low" if effort == "none" else effort}
        return request


class AnthropicChat:
    def __init__(self, provider: AnthropicProvider, system: str, effort: str, history: list[dict] | None) -> None:
        self._provider = provider
        self._system = system
        self._effort = effort
        self._messages: list[dict] = list(history or [])
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def send(self, text: str, image: bytes | None = None) -> Iterator[str]:
        model = self._provider.model
        user_message = {"role": "user", "content": _with_image(text, image) if image else text}
        request = self._provider._request(self._system, self._effort)
        request["messages"] = [*self._messages, user_message]

        self._cancelled = False
        with _errors(model):
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

        if final.stop_reason == "refusal":
            raise AIError("Claude declined to answer this one.")
        # Append-only history: the full response content (thinking blocks included) goes
        # back unchanged on the next turn.
        self._messages += [user_message, {"role": "assistant", "content": final.content}]


def _with_image(text: str, png: bytes) -> list[dict]:
    import base64

    data = base64.b64encode(png).decode()
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}, {"type": "text", "text": text}]


@contextmanager
def _errors(model: str):
    """Turn SDK errors into messages a player can act on."""
    import anthropic

    try:
        yield
    except anthropic.AuthenticationError as e:
        raise AIError("Claude rejected the API key. Check it in Horu's AI settings.") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError(f"This API key can't use {model}.") from e
    except anthropic.NotFoundError as e:
        raise AIError(f"Unknown Claude model: {model}. Check the model in Horu's AI settings.") from e
    except anthropic.RateLimitError as e:
        raise AIError("Claude's rate limit was hit. Wait a moment and try again.") from e
    except anthropic.APIStatusError as e:
        if e.status_code in (500, 502, 503, 504, 529):
            raise AIError(f"Claude is busy right now ({e.status_code}). Try again in a moment.") from e
        raise AIError(f"Claude returned an error ({e.status_code}): {e.message}") from e
    except anthropic.APITimeoutError as e:
        raise AIError("Claude didn't answer in time. Try again in a moment.") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Couldn't reach Claude. Check your internet connection.") from e
    except TypeError as e:
        # The SDK raises TypeError when it finds no credentials at all.
        if "authentication" not in str(e):
            raise
        raise AIError("No Claude API key yet. Add one in Horu's AI settings.") from e
