import pytest

from app.agent.planner import Planner, is_grounded, literals
from app.agent.test_data import TestData
from app.schemas.plan import PlannedCriterion, PlannedStep, PlannerOutput

DATA = TestData.generate("qa123abc")
OBJECTIVE = ('Log in, then log out and verify the user is back on the login page at /login. '
             'Save the email "not-an-email". Verify an order confirmation is shown. Check the cart total.')
STATED = literals(OBJECTIVE, DATA)


@pytest.mark.parametrize("criterion, grounded", [
    (dict(type="text_visible", value="Test User-qa123abc"), True),        # generated test data
    (dict(type="field_value", name="Email", value="not-an-email"), True),  # quoted in the objective
    (dict(type="url_contains", value="/login"), True),                     # path in the objective
    (dict(type="text_visible", value="Order confirmation"), False),        # prose, not a stated value
    (dict(type="text_visible", value="Welcome Test User-qa123abc"), False),
    (dict(type="url_contains", value="/dashboard"), False),
    (dict(type="sum_equals"), True),
    (dict(type="request_succeeded", value="/api/login", method="POST"), False),
    (dict(type="element_present", role="alert"), False),
])
def test_grounding(criterion, grounded):
    assert is_grounded(PlannedCriterion(**criterion), OBJECTIVE, STATED) is grounded


def test_alert_grounded_when_objective_expects_an_error():
    objective = "verify the application rejects it with a validation error"
    assert is_grounded(PlannedCriterion(type="element_present", role="alert"), objective, set())


def test_literals():
    stated = literals('Open /settings/profile, type "Ada" and \'Bob\', see "Are you sure?", mail ann@x.io.', DATA)
    assert {"/settings/profile", "ada", "bob", "are you sure?", "ann@x.io", "test+qa123abc@example.com"} <= stated


def step(goal, *criteria):
    return PlannedStep(goal=goal, criteria=[PlannedCriterion(**c) for c in criteria])


def test_validation_drops_unusable_criteria_but_keeps_the_step():
    planner = Planner(model=None, max_steps=5)
    output = PlannerOutput(steps=[
        step("Log in", dict(type="field_value", name="Email"), dict(type="request_succeeded", value="/login")),
        step("Add to cart", dict(type="field_value", name="Mouse")),
        step("Check", dict(type="url_contains", value="/cart")),
    ])
    planner._validate(output)
    assert [len(s.criteria) for s in output.steps] == [1, 0, 1]


def test_last_step_must_be_checked():
    planner = Planner(model=None, max_steps=5)
    with pytest.raises(ValueError, match="last step"):
        planner._validate(PlannerOutput(steps=[step("Only", dict(type="field_value", name="X"))]))
    with pytest.raises(ValueError, match="1 to 5 steps"):
        planner._validate(PlannerOutput(steps=[]))
