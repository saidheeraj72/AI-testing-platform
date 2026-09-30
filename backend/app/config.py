"""Settings from ai-tester.toml, plus the local data layout."""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "ai-tester.toml"


class ConfigError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSettings(_Strict):
    provider: Literal["ollama", "openai_compatible"] = "ollama"
    base_url: str = "http://localhost:11434"
    name: str = "qwen3:4b"
    api_key: str = ""
    temperature: float = 0.0
    context_window: int = Field(8192, ge=2048)
    think: bool = False
    timeout_seconds: float = Field(180, gt=0)
    max_output_tokens: int = Field(1024, ge=64)
    max_repairs: int = Field(2, ge=0)
    keep_alive: str = "15m"


class ModelRoles(_Strict):
    """[model] plus optional [model.planner] / [model.executor] overrides."""

    default: ModelSettings
    planner: ModelSettings
    executor: ModelSettings


class AgentSettings(_Strict):
    max_plan_steps: int = Field(10, ge=1)
    max_actions_per_step: int = Field(10, ge=1)
    max_verify_failures: int = Field(2, ge=1)
    max_replans: int = Field(2, ge=0)
    max_model_calls: int = Field(80, ge=1)
    max_duration_seconds: float = Field(900, gt=0)
    observation_max_chars: int = Field(6000, ge=1000)


class BrowserSettings(_Strict):
    headless: bool = False
    channel: Literal["chrome", "chromium"] = "chrome"


class SafetySettings(_Strict):
    risky_actions: Literal["confirm", "allow", "block"] = "confirm"


class Settings(_Strict):
    model: ModelRoles
    agent: AgentSettings = AgentSettings()
    browser: BrowserSettings = BrowserSettings()
    safety: SafetySettings = SafetySettings()


def load_settings(path: Path | None = None) -> Settings:
    path = Path(path or os.environ.get("AI_TESTER_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        raw = tomllib.loads(path.read_text()) if path.exists() else {}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from e

    model = dict(raw.pop("model", {}))
    overrides = {role: model.pop(role, None) or {} for role in ("planner", "executor")}
    if key := os.environ.get("AI_TESTER_MODEL_API_KEY"):
        model["api_key"] = key
    try:
        return Settings(
            model=ModelRoles(
                default=ModelSettings(**model),
                **{role: ModelSettings(**(model | o)) for role, o in overrides.items()},
            ),
            **raw,
        )
    except (ValidationError, TypeError) as e:
        raise ConfigError(f"{path}: {e}") from e


@lru_cache
def settings() -> Settings:
    return load_settings()


def data_dir() -> Path:
    """Root for SQLite, session folders and browser profiles.

    Defaults to <repo>/data when running from source. A packaged build sets
    AI_TESTER_DATA_DIR to a per-user application directory.
    """
    return Path(os.environ.get("AI_TESTER_DATA_DIR", _REPO_ROOT / "data")).resolve()


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def profiles_dir() -> Path:
    return data_dir() / "browser_profiles"
