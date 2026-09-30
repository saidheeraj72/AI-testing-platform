import pytest

from app.agent.agent import AgentResult
from app.analysis.bug_analyzer import BugAnalyzer, describe_by_rules
from app.analysis.deduplicator import deduplicate
from app.analysis.report_builder import outcome
from app.browser.console import ConsoleEvent
from app.browser.network import NetworkEvent
from app.detection import signatures
from app.detection.candidates import detect
from app.model.provider import ModelError
from app.safety.domain_scope import DomainScope
from app.schemas.action import ActionResult, Target
from app.schemas.bug import Analysis
from app.schemas.plan import CheckResult, Criterion, Step, StepStatus

SCOPE = DomainScope.from_target("http://localhost:3000")
APP = "http://localhost:3000"


def net(seq, method, path, status=None, failure=None, rtype="fetch", host=APP):
    return NetworkEvent(seq=0, action_seq=seq, method=method, url=f"{host}{path}", resource_type=rtype,
                        first_party=SCOPE.is_first_party(host), main_frame=True, started_at=0,
                        status=status, failure=failure)


def log(seq, text, kind="console", url=f"{APP}/src/app.js", stack=None):
    return ConsoleEvent(seq=0, action_seq=seq, kind=kind, level="error", text=text, url=url, line=1,
                        page_url=APP, at=0, stack=stack)


def action(seq, name="click", target=None, url="/"):
    return ActionResult(sequence=seq, action=name, arguments={}, ok=True, url_before=f"{APP}{url}",
                        url_after=f"{APP}{url}", target=Target(role="button", name=target) if target else None)


def step(seq, goal, first, last, status=StepStatus.PASSED, checks=()):
    return Step(sequence=seq, goal=goal, criteria=[c.criterion for c in checks], status=status,
                first_action=first, last_action=last, checks=list(checks))


def run(network=(), console=(), steps=(), actions=()):
    candidates, _ = detect(network=list(network), console=list(console), steps=list(steps),
                           actions=list(actions), scope=SCOPE)
    return candidates


def test_signatures_ignore_ids_and_numbers():
    assert signatures.path_template(f"{APP}/api/orders/ORD-1001/items/42?x=1") == "/api/orders/:id/items/:id"
    assert signatures.js("TypeError: x is 3 at 'abc'") == signatures.js("TypeError: x is 7 at 'def'")


def test_500_with_console_error_is_one_incident():
    candidates = run(
        network=[net(3, "POST", "/api/customers", 500)],
        console=[log(3, "Request failed with status 500: POST /api/customers"),
                 log(3, "Failed to load resource: the server responded with a status of 500")],
        steps=[step(1, "Create customer", 2, 3)],
        actions=[action(2, "type"), action(3, target="Create customer", url="/customers/new")],
    )
    assert len(candidates) == 1
    c = candidates[0]
    assert c.strong and c.primary.kind == "http_error"
    assert [s.kind for s in c.signals] == ["http_error", "console_error"]  # resource-load line dropped
    assert c.step == 1 and c.action == 'click button "Create customer"'
    assert c.url.endswith("/customers/new")


def test_baseline_errors_are_ignored_later_too():
    candidates = run(
        console=[log(1, "[legacy-widget] failed to init"), log(5, "[legacy-widget] failed to init")],
        network=[net(1, "GET", "/api/me", 401), net(4, "GET", "/api/broken", 500)],
    )
    assert [c.primary.summary for c in candidates] == ["GET /api/broken returned HTTP 500"]


def test_third_party_and_expected_statuses_raise_nothing():
    candidates = run(
        network=[net(2, "GET", "/tracker.js", failure="net::ERR_NAME_NOT_RESOLVED", rtype="script",
                     host="https://analytics.example"),
                 net(2, "GET", "/collect", 500, host="https://analytics.example"),
                 net(2, "POST", "/api/login", 401), net(2, "POST", "/api/customers", 422),
                 net(2, "DELETE", "/api/x", failure="net::ERR_ABORTED"),
                 net(2, "GET", "/api/x", failure="net::ERR_BLOCKED_BY_CLIENT")],
        console=[log(2, "boom", url="https://cdn.other.com/lib.js"),
                 log(2, "TypeError: x", kind="pageerror", url=None, stack="at f (https://cdn.other.com/lib.js:1)")],
    )
    assert candidates == []


def test_uncaught_exception_is_strong_console_error_is_weak():
    candidates = run(console=[log(2, "TypeError: boom", kind="pageerror", url=None,
                                  stack=f"at f ({APP}/src/app.js:3)"),
                              log(3, "Something odd")])
    assert [(c.primary.kind, c.strong) for c in candidates] == [("js_exception", True), ("console_error", False)]


def test_404_from_agent_typed_url_is_not_a_signal():
    candidates = run(
        network=[net(2, "GET", "/products/made-up", 404, rtype="document"),
                 net(3, "GET", "/api/items/9", 404)],
        actions=[action(2, "navigate"), action(3, target="Open item")],
    )
    assert [c.primary.summary for c in candidates] == ["GET /api/items/:id returned HTTP 404"]
    assert not candidates[0].strong


def test_grounded_failed_check_becomes_a_candidate():
    grounded = CheckResult(criterion=Criterion(type="url_contains", value="/login", grounded=True),
                           passed=False, detail="current URL is /dashboard")
    guessed = CheckResult(criterion=Criterion(type="text_visible", value="Saved"), passed=False, detail="x")
    app_err = CheckResult(criterion=Criterion(type="request_succeeded", value="/api/x"), passed=False,
                          detail="500", app_error=True)
    candidates = run(steps=[step(2, "Log out", 3, 5, StepStatus.FAILED, [grounded, guessed, app_err])])
    assert len(candidates) == 1
    c = candidates[0]
    assert c.primary.kind == "assertion_failure" and c.action_seq == 5
    assert c.failed_check == "URL contains '/login'"


def test_dedup_merges_occurrences():
    first, second = run(
        network=[net(3, "POST", "/api/orders/1", 500), net(7, "POST", "/api/orders/2", 500)],
        steps=[step(1, "Order A", 2, 4), step(2, "Order B", 5, 8)],
    )
    analysis = describe_by_rules(first)
    bugs = deduplicate([(first, analysis, "rules", []), (second, analysis, "rules", [])])
    assert len(bugs) == 1
    assert [o.step for o in bugs[0].occurrences] == [1, 2]
    assert len(bugs[0].network) == 2


class _Model:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    async def generate(self, *a, **k):
        if self.error:
            raise self.error
        return self.reply


def _analysis(is_bug):
    return Analysis(is_bug=is_bug, title="t", severity="low", category="other", summary="s", expected="e", actual="a")


async def test_analyzer_cannot_reject_strong_but_decides_weak():
    strong, weak = run(network=[net(2, "GET", "/api/a", 500), net(3, "GET", "/api/b", 404)])
    rejecting = BugAnalyzer(_Model(_analysis(False)), "obj")
    assert (await rejecting.analyze(strong))[0].is_bug
    assert (await rejecting.analyze(weak))[0] is None
    assert (await BugAnalyzer(_Model(_analysis(True)), "obj").analyze(weak))[0] is not None


async def test_without_a_model_strong_is_described_by_rules_weak_stays_unconfirmed():
    strong, weak = run(network=[net(2, "POST", "/api/a", 500), net(3, "GET", "/api/b", 404)])
    analyzer = BugAnalyzer(_Model(error=ModelError("down")), "obj")
    analysis, by = await analyzer.analyze(strong)
    assert by == "rules" and analysis.title.startswith("POST /api/a returns HTTP 500")
    assert (await analyzer.analyze(weak))[0] is None
    assert (await BugAnalyzer(None, "obj").analyze(weak))[0] is None


@pytest.mark.parametrize("agent, has_bugs, expected", [
    ("PASS", True, "BUGS_FOUND"),          # e.g. a JS exception during a passing workflow
    ("BUGS_FOUND", False, "COULD_NOT_VERIFY"),
    ("COULD_NOT_VERIFY", False, "COULD_NOT_VERIFY"),
    ("CANCELLED", False, "CANCELLED"),
    ("FAILED", True, "BUGS_FOUND"),
])
def test_outcome(agent, has_bugs, expected):
    bugs = [object()] if has_bugs else []
    assert outcome(AgentResult(outcome=agent, reason="", steps=[]), bugs) == expected


def test_failed_check_in_a_step_without_actions_belongs_to_that_step():
    grounded = CheckResult(criterion=Criterion(type="sum_equals", grounded=True), passed=False, detail="wrong total")
    verify = step(5, "Verify the total", 9, 8, StepStatus.FAILED, [grounded])  # no actions: first 9 > last 8
    verify.end_url = f"{APP}/cart"
    candidates = run(steps=[step(4, "Open the cart", 8, 8), verify],
                     actions=[action(8, target="Cart", url="/shop")])
    c = candidates[0]
    assert (c.step, c.url, c.action) == (5, f"{APP}/cart", None)
