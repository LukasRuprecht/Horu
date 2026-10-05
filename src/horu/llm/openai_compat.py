"""Any API that speaks the OpenAI chat-completions format: OpenAI, Gemini, OpenRouter, and
local servers such as Ollama and LM Studio."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

from horu.llm.base import AIError
from horu.llm.tools import Tool


class OpenAICompatProvider:
    def __init__(self, model: str, base_url: str, api_key: str = "", client=None) -> None:
        self.model = model
        self.base_url = base_url
        host = urlparse(base_url).hostname or base_url
        self.label = f"{model} ({host})"
        if client is None:
            import openai

            # Local servers such as Ollama ignore the key, but the client requires one.
            client = openai.OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._client = client

    def chat(
        self, system: str, effort: str = "low", history: list[dict] | None = None, temperature: float | None = None
    ) -> OpenAICompatChat:
        return OpenAICompatChat(self, system, history, temperature)

    def run_tools(
        self, system: str, task: str, tools: list[Tool], effort: str = "low", max_turns: int = 12, follow_up=None
    ) -> str:
        """Let the model work on `task`, calling `tools` as it decides, until it answers.
        Needs a model with tool support (for Ollama, e.g. qwen3 or llama3.2)."""
        by_name = {tool.name: tool for tool in tools}
        definitions = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]
        messages: list[dict] = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": task}]
        for _ in range(max_turns):
            with _errors(self):
                response = self._client.chat.completions.create(model=self.model, messages=messages, tools=definitions)
            message = response.choices[0].message
            calls = message.tool_calls or []
            assistant: dict = {"role": "assistant", "content": message.content or ""}
            if calls:
                assistant["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ]
            messages.append(assistant)
            if not calls:
                answer = (message.content or "").strip()
                nudge = follow_up(answer) if follow_up else None
                if nudge is None:
                    return answer
                messages.append({"role": "user", "content": nudge})
                continue
            for call in calls:
                tool = by_name.get(call.function.name)
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except ValueError:
                    arguments = None
                if tool is None:
                    result = f"Error: no tool named {call.function.name}"
                elif not isinstance(arguments, dict):
                    result = "Error: the tool input wasn't valid JSON."
                else:
                    result, _ = tool.call(arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        raise AIError("That took more steps than expected, so Horu stopped it.")


class OpenAICompatChat:
    def __init__(
        self, provider: OpenAICompatProvider, system: str, history: list[dict] | None, temperature: float | None
    ) -> None:
        self._provider = provider
        self._temperature = temperature
        # Without a system message, a local model keeps the one it was built with (useful for
        # specialised translation models).
        self._messages: list[dict] = [{"role": "system", "content": system}] if system else []
        self._messages += list(history or [])
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def send(self, text: str) -> Iterator[str]:
        provider = self._provider
        user_message = {"role": "user", "content": text}
        reply = []
        self._cancelled = False
        with _errors(provider):
            extra = {} if self._temperature is None else {"temperature": self._temperature}
            stream = provider._client.chat.completions.create(
                model=provider.model, messages=[*self._messages, user_message], stream=True, **extra
            )
            for chunk in stream:
                if self._cancelled:
                    stream.close()
                    return
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    reply.append(delta)
                    yield delta

        self._messages += [user_message, {"role": "assistant", "content": "".join(reply)}]


@contextmanager
def _errors(provider: OpenAICompatProvider):
    """Turn SDK errors into messages a player can act on."""
    import openai

    try:
        yield
    except openai.AuthenticationError as e:
        raise AIError(f"{provider.base_url} rejected the API key.") from e
    except openai.NotFoundError as e:
        hint = f" For Ollama, run `ollama pull {provider.model}`." if _is_local(provider.base_url) else ""
        raise AIError(f"Model not found: {provider.model}.{hint}") from e
    except openai.RateLimitError as e:
        raise AIError("Rate limit hit. Wait a moment and try again.") from e
    except openai.BadRequestError as e:
        if "tool" in str(e).lower():
            raise AIError(f"{provider.model} can't use tools, which this needs. Choose a model with tool support (for Ollama, e.g. qwen3).") from e
        raise AIError(f"The AI provider rejected the request ({e.status_code}).") from e
    except openai.APIStatusError as e:
        raise AIError(f"The AI provider returned an error ({e.status_code}).") from e
    except openai.APIConnectionError as e:
        hint = " Is Ollama (or your local server) running?" if _is_local(provider.base_url) else ""
        raise AIError(f"Couldn't reach {provider.base_url}.{hint}") from e


def _is_local(url: str) -> bool:
    return urlparse(url).hostname in ("localhost", "127.0.0.1", "::1")
