"""Any API that speaks the OpenAI chat-completions format: OpenAI, Gemini, OpenRouter, and
local servers such as Ollama and LM Studio."""

from __future__ import annotations

import os
from typing import Iterator
from urllib.parse import urlparse

from horu.llm.base import AIError


class OpenAICompatProvider:
    def __init__(self, model: str, base_url: str, api_key_env: str = "", client=None) -> None:
        self.model = model
        self.base_url = base_url
        host = urlparse(base_url).hostname or base_url
        self.label = f"{model} ({host})"
        if client is None:
            import openai

            api_key = os.environ.get(api_key_env, "") if api_key_env else ""
            if api_key_env and not api_key:
                raise AIError(f"No API key found. Put {api_key_env} in your .env file.")
            # Local servers such as Ollama ignore the key, but the client requires one.
            client = openai.OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._client = client

    def chat(self, system: str, effort: str = "low") -> OpenAICompatChat:
        return OpenAICompatChat(self, system)


class OpenAICompatChat:
    def __init__(self, provider: OpenAICompatProvider, system: str) -> None:
        self._provider = provider
        self._messages: list[dict] = [{"role": "system", "content": system}]
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def send(self, text: str) -> Iterator[str]:
        import openai

        provider = self._provider
        user_message = {"role": "user", "content": text}
        reply = []
        self._cancelled = False
        try:
            stream = provider._client.chat.completions.create(
                model=provider.model, messages=[*self._messages, user_message], stream=True
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
        except openai.AuthenticationError as e:
            raise AIError(f"{provider.base_url} rejected the API key.") from e
        except openai.NotFoundError as e:
            hint = f" For Ollama, run `ollama pull {provider.model}`." if _is_local(provider.base_url) else ""
            raise AIError(f"Model not found: {provider.model}.{hint}") from e
        except openai.RateLimitError as e:
            raise AIError("Rate limit hit. Wait a moment and try again.") from e
        except openai.APIStatusError as e:
            raise AIError(f"The AI provider returned an error ({e.status_code}).") from e
        except openai.APIConnectionError as e:
            hint = " Is Ollama (or your local server) running?" if _is_local(provider.base_url) else ""
            raise AIError(f"Couldn't reach {provider.base_url}.{hint}") from e

        self._messages += [user_message, {"role": "assistant", "content": "".join(reply)}]


def _is_local(url: str) -> bool:
    return urlparse(url).hostname in ("localhost", "127.0.0.1", "::1")
