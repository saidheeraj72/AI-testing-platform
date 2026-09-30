"""Ollama's native /api/chat, for local and cloud models.

Used instead of Ollama's OpenAI-compatible endpoint because only the native
API can set the context window (num_ctx) per request. The default of 4096
tokens would silently truncate page observations.

Cloud models (names ending in "-cloud" or ":cloud") run through the local
`ollama serve` after `ollama signin`, or directly against https://ollama.com
with an API key.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import ModelSettings
from app.model.provider import Completion, Message, ModelError, ModelProvider


class OllamaProvider(ModelProvider):
    def __init__(self, settings: ModelSettings):
        super().__init__(settings)
        headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
        self._client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"), timeout=settings.timeout_seconds, headers=headers
        )

    async def complete(
        self, messages: list[Message], schema: dict[str, Any], *, temperature: float | None = None
    ) -> Completion:
        s = self.settings
        temperature = s.temperature if temperature is None else temperature
        body = {
            "model": s.name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "format": schema,
            "think": s.think,
            "keep_alive": s.keep_alive,
            "options": {"temperature": temperature, "num_ctx": s.context_window, "num_predict": s.max_output_tokens},
        }
        try:
            response = await self._client.post("/api/chat", json=body)
        except httpx.TimeoutException:
            raise ModelError(f"Ollama did not answer within {s.timeout_seconds:.0f}s") from None
        except httpx.HTTPError as e:
            raise ModelError(f"Cannot reach Ollama at {s.base_url} ({e}). Is `ollama serve` running?") from None
        if response.status_code == 401:
            raise ModelError(
                "Ollama cloud model needs sign-in: run `ollama signin`, "
                "or set AI_TESTER_MODEL_API_KEY with base_url = \"https://ollama.com\""
            )
        if response.status_code == 404:
            raise ModelError(f"Ollama has no model {s.name!r}. Run: ollama pull {s.name}")
        if response.status_code >= 400:
            raise ModelError(f"Ollama error {response.status_code}: {response.text[:300]}")

        data = response.json()
        return Completion(
            text=data.get("message", {}).get("content", ""),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )

    async def close(self) -> None:
        await self._client.aclose()
