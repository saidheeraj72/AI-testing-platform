"""Any OpenAI-compatible /chat/completions endpoint (LM Studio, llama.cpp, vLLM, hosted APIs)."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import ModelSettings
from app.model.provider import Completion, Message, ModelError, ModelProvider


class OpenAICompatibleProvider(ModelProvider):
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
        body: dict[str, Any] = {
            "model": s.name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": s.max_output_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}},
        }
        if s.think is False:
            body["reasoning_effort"] = "none"
        elif isinstance(s.think, str):
            body["reasoning_effort"] = s.think
        try:
            response = await self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException:
            raise ModelError(f"Model endpoint did not answer within {s.timeout_seconds:.0f}s") from None
        except httpx.HTTPError as e:
            raise ModelError(f"Cannot reach model endpoint {s.base_url} ({e})") from None
        if response.status_code >= 400:
            raise ModelError(f"Model endpoint error {response.status_code}: {response.text[:300]}")

        data = response.json()
        usage = data.get("usage") or {}
        return Completion(
            text=(data.get("choices") or [{}])[0].get("message", {}).get("content") or "",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def close(self) -> None:
        await self._client.aclose()
