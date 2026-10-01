"""Structured model calls: schema-constrained output, validation, repair, logging, budget.

Every call is appended to the session's model_calls.jsonl, including failed
attempts, because that file is the main tool for debugging agent behaviour.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.model.provider import Message, ModelProvider
from app.storage.manager import SessionStorage

T = TypeVar("T", bound=BaseModel)
REPAIR_TEMPERATURE = 0.5


class InvalidModelOutput(RuntimeError):
    """The model kept producing output that fails validation."""


class StructuredModel:
    def __init__(
        self,
        provider: ModelProvider,
        storage: SessionStorage,
        *,
        before_call: Callable[[], None] = lambda: None,
    ):
        self.provider = provider
        self.storage = storage
        self._before_call = before_call  # budget hook; raises to stop the session
        self.calls = 0

    async def generate(
        self,
        schema: type[T],
        messages: list[Message],
        *,
        purpose: str,
        step: int | None = None,
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Call the model until its reply parses into `schema` and passes `validate`.

        `validate` raises ValueError with a message the model can act on.
        Invalid replies are sent back with the error, up to max_repairs times.
        """
        json_schema = schema.model_json_schema()
        # Some backends (e.g. some Ollama cloud models) ignore the API's schema parameter,
        # so the schema is also stated in the prompt.
        conversation = _with_schema_instruction(list(messages), json_schema)
        for attempt in range(self.provider.settings.max_repairs + 1):
            self._before_call()
            self.calls += 1
            started = time.monotonic()
            # A deterministic model repeats the same mistake; loosen sampling for repairs.
            temperature = None if attempt == 0 else max(self.provider.settings.temperature, REPAIR_TEMPERATURE)
            completion = await self.provider.complete(conversation, json_schema, temperature=temperature)
            latency_ms = int((time.monotonic() - started) * 1000)

            error: str | None = None
            parsed: T | None = None
            try:
                parsed = schema.model_validate_json(extract_json(completion.text))
                if validate:
                    validate(parsed)
            except ValidationError as e:
                error = _short_validation_error(e)
            except ValueError as e:
                error = str(e)

            self.storage.append_jsonl(self.storage.paths.model_calls, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "purpose": purpose,
                "step": step,
                "attempt": attempt,
                "model": self.provider.settings.name,
                "input_tokens": completion.prompt_tokens,
                "output_tokens": completion.completion_tokens,
                "latency_ms": latency_ms,
                "response_type": getattr(parsed, "action", None) or schema.__name__,
                "validation_error": error,
                "prompt": [m.log() for m in conversation],
                "response": completion.text,
            })
            if error is None and parsed is not None:
                return parsed
            conversation = conversation + [
                Message("assistant", completion.text),
                Message("user", f"That reply was invalid: {error}\nReply again with corrected JSON only."),
            ]
        raise InvalidModelOutput(f"{purpose}: model output still invalid after retries: {error}")


def _with_schema_instruction(messages: list[Message], json_schema: dict) -> list[Message]:
    instruction = (
        "\n\nReply with one JSON object only (no markdown, no code fences) that matches this JSON schema. "
        "Use exactly these field names:\n" + json.dumps(json_schema, separators=(",", ":"))
    )
    if messages and messages[0].role == "system":
        return [Message("system", messages[0].content + instruction, messages[0].images), *messages[1:]]
    return [Message("system", instruction.strip()), *messages]


def extract_json(text: str) -> str:
    """The JSON object in a reply, without code fences, surrounding prose or trailing commas."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if not text.startswith("{") and fenced:
        text = fenced[1]
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        text = text[start:end + 1] if 0 <= start < end else text
    return _TRAILING_COMMA.sub(r"\1", text)


# {"a": 1,} -> {"a": 1}. Only outside strings would be exact; a comma before a closing bracket inside a
# string value is rare enough in these replies (short reasoning sentences) to accept.
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def _short_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(p) for p in err["loc"]) or "root"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts) if parts else json.dumps(e.errors()[:3], default=str)
