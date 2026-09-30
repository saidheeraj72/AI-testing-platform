import pytest

from app.agent.planner import Planner, is_grounded
from app.agent.test_data import TestData
from app.schemas.plan import PlannedCriterion, PlannedStep, PlannerOutput

DATA = TestData.generate("qa123abc")
OBJECTIVE = "Log in, log out and verify the user is back on the login page. Check the cart total."
CORPUS = " ".join([OBJECTIVE, *DATA.model_dump().values()]).casefold()


@pytest.mark.parametrize("criterion, grounded", [
    (dict(type="text_visible", value="Test User-qa123abc"), True),
    (dict(type="text_visible", value="Customer created"), False),
    (dict(type="url_contains", value="/login"), True),
    (dict(type="url_contains", value="/dashboard"), False),
    (dict(type="sum_equals"), True),
    (dict(type="request_succeeded", value="/api/login", method="POST"), False),
    (dict(type="element_present", role="alert"), False),
])
def test_grounding(criterion, grounded):
    assert is_grounded(PlannedCriterion(**criterion), CORPUS) is grounded


def test_alert_grounded_when_objective_expects_an_error():
    corpus = "verify the application rejects it with a validation error"
    assert is_grounded(PlannedCriterion(type="element_present", role="alert"), corpus)


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
