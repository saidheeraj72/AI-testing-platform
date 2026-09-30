import pytest

from app.agent.budgets import BudgetExceeded, SessionBudget
from app.agent.decision import Decision, validator_for
from app.agent.loops import LoopDetector
from app.config import AgentSettings, ConfigError, load_settings
from app.safety.risk import risky_reason
from app.schemas.observation import Element, Observation, Viewport


def test_loop_detector():
    loops = LoopDetector()
    assert loops.record_action(("click", "e1", "")) is None
    assert loops.record_action(("click", "e1", "")) is None
    assert "repeated" in loops.record_action(("click", "e1", ""))
    for _ in range(4):
        assert loops.record_page("same") is None
    assert "did not change" in loops.record_page("same")
    loops.reset()
    assert loops.record_page("same") is None


def test_budget_limits_model_calls():
    budget = SessionBudget(AgentSettings(max_model_calls=2))
    budget.before_model_call()
    budget.before_model_call()
    with pytest.raises(BudgetExceeded):
        budget.before_model_call()


@pytest.mark.parametrize("name, risky", [
    ("Delete customer", True), ("Place order", True), ("Pay now", True), ("Send message", True),
    ("Remove Wireless Mouse", True), ("Save settings", False), ("Search", False),
    ("Proceed to checkout", False), ("Sender details", False),
])
def test_risky_labels(name, risky):
    assert (risky_reason("click", Element(ref="e1", role="button", name=name)) is not None) is risky


def test_decision_validator():
    obs = Observation(sequence=1, url="u", title="t", viewport=Viewport(width=1, height=1),
                      elements=[Element(ref="e5", role="button", name="Go")], text="", fingerprint="f")
    validate = validator_for(obs)
    validate(Decision(reasoning="", action="click", ref="e5"))
    validate(Decision(reasoning="", action="verify"))
    with pytest.raises(ValueError, match="not on the current page"):
        validate(Decision(reasoning="", action="click", ref="e9"))
    with pytest.raises(ValueError, match="needs 'text'"):
        validate(Decision(reasoning="", action="type", ref="e5"))


def test_settings_file_and_role_overrides(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[model]\nname = "big"\ncontext_window = 16384\n[model.planner]\nthink = true\n'
                   '[agent]\nmax_replans = 0\n')
    s = load_settings(cfg)
    assert (s.model.planner.name, s.model.planner.think, s.model.planner.context_window) == ("big", True, 16384)
    assert (s.model.executor.name, s.model.executor.think) == ("big", False)
    assert s.agent.max_replans == 0

    cfg.write_text('[model]\nnmae = "typo"\n')
    with pytest.raises(ConfigError, match="nmae"):
        load_settings(cfg)


def test_repo_config_is_valid():
    assert load_settings().model.default.provider == "ollama"


def test_extract_json_from_fenced_or_wrapped_replies():
    from app.model.client import extract_json

    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Here you go: {"a": {"b": 2}} done') == '{"a": {"b": 2}}'
