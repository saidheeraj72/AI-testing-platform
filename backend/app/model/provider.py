"""The model interface: one raw chat call that returns text constrained by a JSON schema."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.config import ModelSettings


class ModelError(RuntimeError):
    """The model endpoint failed (unreachable, HTTP error, timeout, model missing)."""


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str


@dataclass
class Completion:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelProvider(ABC):
    def __init__(self, settings: ModelSettings):
        self.settings = settings

    @abstractmethod
    async def complete(
        self, messages: list[Message], schema: dict[str, Any], *, temperature: float | None = None
    ) -> Completion:
        """Return the model's reply, constrained to `schema` where the backend supports it.

        `temperature` overrides the configured one for this call.
        """

    async def close(self) -> None:
        pass


def create_provider(settings: ModelSettings) -> ModelProvider:
    if settings.provider == "ollama":
        from app.model.ollama import OllamaProvider

        return OllamaProvider(settings)
    from app.model.openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(settings)
