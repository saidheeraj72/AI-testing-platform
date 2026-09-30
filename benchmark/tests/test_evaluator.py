import json
from pathlib import Path

import pytest

from benchmark.evaluator.cli import main
from benchmark.evaluator.scoring import score_session, summarize
from benchmark.evaluator.spec import SpecError, load_spec

SPEC = load_spec()

HTTP_500_BUG = {
    "title": "Customer creation returns HTTP 500",
    "url": "http://localhost:3000/customers/new",
    "evidence": {
        "network": [{"method": "POST", "url": "http://localhost:3000/api/customers", "status": 500}],
        "console": ["Request failed with status 500: POST /api/customers"],
    },
}
NOISE_BUG = {
    "title": "Script failed to load",
    "evidence": {"console": ["Failed to load https://analytics.seeded-app.invalid/tracker.js"]},
}


def make_session(tmp_path: Path, report: dict, manifest: dict | None = None, calls: list | None = None) -> Path:
    session = tmp_path / "s_test"
    session.mkdir()
    (session / "report.json").write_text(json.dumps(report))
    if manifest is not None:
        (session / "manifest.json").write_text(json.dumps(manifest))
    if calls is not None:
        (session / "model_calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls))
    return session


def test_real_spec_is_consistent():
    assert set(SPEC.bugs) == {"BUG-001", "BUG-002", "BUG-003", "BUG-004"}
    seeded = {b for s in SPEC.scenarios.values() for b in s.bugs}
    assert seeded == set(SPEC.bugs), "every seeded bug needs a scenario"
    assert any(not s.bugs for s in SPEC.scenarios.values()), "need false-positive control scenarios"


def test_perfect_run(tmp_path):
    session = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [HTTP_500_BUG]})
    r = score_session(session, SPEC, scenario_id="customer-create", active="all")
    assert r.found == ["BUG-001"]
    assert r.missed == []
    assert r.false_positives == []
    assert r.outcome_correct


def test_missed_bug_and_wrong_outcome(tmp_path):
    session = make_session(tmp_path, {"outcome": "COULD_NOT_VERIFY", "bugs": []})
    r = score_session(session, SPEC, scenario_id="customer-create", active="all")
    assert r.missed == ["BUG-001"]
    assert not r.outcome_correct


def test_noise_is_labelled_false_positive(tmp_path):
    session = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [HTTP_500_BUG, NOISE_BUG]})
    r = score_session(session, SPEC, scenario_id="customer-create", active="all")
    assert [fp.label for fp in r.false_positives] == ["noise:NOISE-001"]


def test_clean_app_expects_pass(tmp_path):
    session = make_session(tmp_path, {"outcome": "PASS", "bugs": []})
    r = score_session(session, SPEC, scenario_id="customer-create", active="none")
    assert r.expected_outcome == "PASS"
    assert r.outcome_correct
    assert r.missed == []


def test_reporting_disabled_bug_is_false_positive(tmp_path):
    session = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [HTTP_500_BUG]})
    r = score_session(session, SPEC, scenario_id="browse-customers", active="none")
    assert [fp.label for fp in r.false_positives] == ["inactive:BUG-001"]


def test_active_bug_outside_scenario_is_incidental_not_fp(tmp_path):
    session = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [HTTP_500_BUG]})
    r = score_session(session, SPEC, scenario_id="browse-customers", active="all")
    assert r.false_positives == []
    assert r.reported[0].label == "incidental:BUG-001"


def test_duplicates_counted_separately(tmp_path):
    session = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [HTTP_500_BUG, HTTP_500_BUG]})
    r = score_session(session, SPEC, scenario_id="customer-create", active="all")
    assert r.found == ["BUG-001"]
    assert len(r.duplicates) == 1
    assert r.false_positives == []


def test_assertion_bug_needs_url_and_keyword(tmp_path):
    right = {"title": "Invalid email accepted", "url": "http://localhost:3000/settings"}
    wrong_page = {"title": "Invalid email accepted", "url": "http://localhost:3000/customers/new"}
    session = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [wrong_page, right]})
    r = score_session(session, SPEC, scenario_id="settings-email-validation", active="all")
    assert r.found == ["BUG-003"]
    assert [fp.label for fp in r.false_positives] == ["unmatched"]


def test_manifest_supplies_scenario_and_metrics(tmp_path):
    session = make_session(
        tmp_path,
        {"outcome": "PASS", "steps": [{"status": "PASSED"}, {"status": "PASSED"}], "bugs": []},
        manifest={"duration_ms": 42000, "benchmark": {"scenario": "logout-redirect", "active_bugs": []}},
        calls=[{"latency_ms": 100, "validation_error": None}, {"latency_ms": 50, "validation_error": "bad json"}],
    )
    r = score_session(session, SPEC)
    assert r.scenario == "logout-redirect"
    assert r.outcome_correct
    assert r.metrics["steps_total"] == 2
    assert r.metrics["model_calls"] == 2
    assert r.metrics["invalid_model_calls"] == 1

    summary = summarize([r])
    assert summary["invalid_model_call_rate"] == 0.5
    assert summary["precision"] is None and summary["recall"] is None


def test_summary_precision_recall(tmp_path):
    a = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [HTTP_500_BUG, NOISE_BUG]})
    r1 = score_session(a, SPEC, scenario_id="customer-create", active="all")
    r2 = score_session(a, SPEC, scenario_id="cart-total", active="all")  # misses BUG-002
    s = summarize([r1, r2])
    assert s["true_positives"] == 1
    assert s["false_negatives"] == 1
    assert s["recall"] == 0.5


def test_errors(tmp_path):
    session = make_session(tmp_path, {"outcome": "PASS", "bugs": []})
    with pytest.raises(SpecError, match="no scenario"):
        score_session(session, SPEC)
    with pytest.raises(SpecError, match="Unknown scenario"):
        score_session(session, SPEC, scenario_id="nope")
    with pytest.raises(SpecError, match="Unknown bug ids"):
        score_session(session, SPEC, scenario_id="customer-create", active="BUG-999")


def test_spec_rejects_unknown_scenario_bug(tmp_path):
    bad = tmp_path / "spec.yaml"
    bad.write_text(
        "bugs: [{id: BUG-001, title: t, match: {keywords_any: [x]}}]\n"
        "scenarios: [{id: s, objective: o, bugs: [BUG-002]}]\n"
    )
    with pytest.raises(SpecError, match="unknown bugs"):
        load_spec(bad)


def test_cli_json(tmp_path, capsys):
    session = make_session(tmp_path, {"outcome": "BUGS_FOUND", "bugs": [HTTP_500_BUG]})
    code = main(["score", str(session), "--scenario", "customer-create", "--active", "all", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["summary"]["recall"] == 1.0


def test_cli_error_exit_code(tmp_path, capsys):
    session = make_session(tmp_path, {"outcome": "PASS", "bugs": []})
    assert main(["score", str(session)]) == 2
    assert "no scenario" in capsys.readouterr().err
