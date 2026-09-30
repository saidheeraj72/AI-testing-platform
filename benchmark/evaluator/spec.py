"""Loads and validates benchmark/expected-results.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SPEC_PATH = Path(__file__).resolve().parent.parent / "expected-results.yaml"


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class NetworkMatch:
    method: str | None = None
    url_contains: str | None = None
    status: int | None = None

    def matches(self, item: dict[str, Any]) -> bool:
        if self.method and str(item.get("method", "")).upper() != self.method.upper():
            return False
        if self.url_contains and self.url_contains not in str(item.get("url", "")):
            return False
        if self.status is not None and item.get("status") != self.status:
            return False
        return True


@dataclass(frozen=True)
class MatchRule:
    network: NetworkMatch | None = None
    url_contains: str | None = None
    keywords_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnownIssue:
    id: str
    title: str
    rule: MatchRule
    is_noise: bool
    detection: str | None = None
    workflow: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    objective: str
    bugs: tuple[str, ...]


@dataclass(frozen=True)
class Spec:
    app_url: str
    bugs: dict[str, KnownIssue]
    noise: dict[str, KnownIssue]
    scenarios: dict[str, Scenario]

    def scenario(self, scenario_id: str) -> Scenario:
        try:
            return self.scenarios[scenario_id]
        except KeyError:
            raise SpecError(
                f"Unknown scenario {scenario_id!r}. Known: {', '.join(self.scenarios)}"
            ) from None

    def parse_active(self, raw: str | list[str] | None) -> frozenset[str]:
        """'all', 'none', 'BUG-001,BUG-003' or a list of ids -> set of active bug ids."""
        if raw is None or raw == "all":
            return frozenset(self.bugs)
        if raw == "none":
            return frozenset()
        ids = [s.strip().upper() for s in raw.split(",")] if isinstance(raw, str) else list(raw)
        ids = [i for i in ids if i]
        unknown = [i for i in ids if i not in self.bugs]
        if unknown:
            raise SpecError(f"Unknown bug ids: {', '.join(unknown)}")
        return frozenset(ids)


def load_spec(path: Path = DEFAULT_SPEC_PATH) -> Spec:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except OSError as e:
        raise SpecError(f"Cannot read spec {path}: {e}") from e
    if not isinstance(data, dict):
        raise SpecError(f"{path}: expected a mapping at the top level")

    bugs = {b.id: b for b in (_issue(raw, is_noise=False) for raw in data.get("bugs", []))}
    noise = {n.id: n for n in (_issue(raw, is_noise=True) for raw in data.get("noise", []))}
    if len(bugs) != len(data.get("bugs", [])) or len(noise) != len(data.get("noise", [])):
        raise SpecError("Duplicate bug or noise id")
    if set(bugs) & set(noise):
        raise SpecError(f"Ids used for both bugs and noise: {set(bugs) & set(noise)}")

    scenarios: dict[str, Scenario] = {}
    for raw in data.get("scenarios", []):
        scenario = Scenario(
            id=_required(raw, "id", "scenario"),
            objective=_required(raw, "objective", "scenario"),
            bugs=tuple(raw.get("bugs") or ()),
        )
        unknown = [b for b in scenario.bugs if b not in bugs]
        if unknown:
            raise SpecError(f"Scenario {scenario.id} references unknown bugs: {unknown}")
        if scenario.id in scenarios:
            raise SpecError(f"Duplicate scenario id {scenario.id}")
        scenarios[scenario.id] = scenario

    return Spec(
        app_url=(data.get("app") or {}).get("url", ""),
        bugs=bugs,
        noise=noise,
        scenarios=scenarios,
    )


def _issue(raw: dict[str, Any], *, is_noise: bool) -> KnownIssue:
    kind = "noise" if is_noise else "bug"
    issue_id = _required(raw, "id", kind)
    match = raw.get("match") or {}
    network = match.get("network")
    rule = MatchRule(
        network=NetworkMatch(**network) if network else None,
        url_contains=match.get("url_contains"),
        keywords_any=tuple(k.lower() for k in match.get("keywords_any") or ()),
    )
    if not (rule.network or rule.url_contains or rule.keywords_any):
        raise SpecError(f"{kind} {issue_id} has an empty match rule")
    return KnownIssue(
        id=issue_id,
        title=_required(raw, "title", kind),
        rule=rule,
        is_noise=is_noise,
        detection=raw.get("detection"),
        workflow=raw.get("workflow"),
    )


def _required(raw: dict[str, Any], key: str, kind: str) -> Any:
    if not raw.get(key):
        raise SpecError(f"A {kind} entry is missing {key!r}: {raw}")
    return raw[key]
