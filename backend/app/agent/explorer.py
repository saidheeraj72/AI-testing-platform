"""Exploration mode: "find bugs in this application" without a specific objective.

1. Log in if needed: with credentials from the notes, the agent does it;
   otherwise the user is asked (like any login wall).
2. Crawl in code: breadth-first over in-scope links, recording what each page
   offers. Links that would end the session or destroy data are not followed.
   Errors seen while loading pages (5xx, broken links, uncaught exceptions)
   are picked up by the normal detectors after the run.
3. Workflows: the model proposes a few safe user workflows from the site
   map; each runs as an ordinary test (planner + executor + checks) in the
   same browser, numbered on after the previous one.

The workflow objectives are written by a model, so nothing in them counts as
a stated expectation: a failed check there is "could not verify", never a
bug. Exploration reports evidence: server errors, broken links, crashes.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from pydantic import BaseModel, Field

from app.agent.agent import AgentResult, AskUserFn, ConfirmFn, EventSink, TestAgent
from app.agent.blockers import detect_blocker
from app.agent.budgets import BudgetExceeded, SessionBudget
from app.agent.control import SessionControl
from app.agent.test_data import TestData
from app.browser.session import BrowserClosedError, BrowserSession
from app.config import Settings
from app.detection.signatures import path_template
from app.model.client import InvalidModelOutput, StructuredModel
from app.model.provider import Message, ModelError
from app.schemas.observation import Observation
from app.schemas.plan import StepStatus

DEFAULT_OBJECTIVE = "Find bugs in this application."
# Links never followed while crawling: they end the session, destroy data or leave the app's normal flow.
_DO_NOT_FOLLOW = re.compile(
    r"log ?out|sign ?out|log off|delete|remove|destroy|unsubscribe|deactivate|cancel (my )?(account|subscription)",
    re.I,
)

WORKFLOW_SYSTEM = """\
You plan exploratory tests for a website. You get a map of the pages found by crawling it. Propose user \
workflows worth testing: the main things a user does on this site (creating or editing records, searching, \
forms, carts, settings). Each objective is tested by a browser agent.

Rules:
- At most {count} workflows, most important first.
- Each objective is one or two sentences a tester could follow, starting from the home page.
- Never propose deleting data, paying, placing real orders, sending messages or changing passwords.
- Prefer workflows that submit forms or change data, because those find most bugs.
- Mention pages and labels as they appear in the map.
Reply with JSON only."""


class ProposedWorkflow(BaseModel):
    title: str = Field(description="3-6 words")
    objective: str


class WorkflowPlan(BaseModel):
    workflows: list[ProposedWorkflow]


@dataclass
class PageInfo:
    url: str
    title: str
    headings: list[str]
    links: int
    buttons: list[str]
    fields: list[str]
    status: int | None = None
    error: str | None = None

    def describe(self) -> str:
        path = urlsplit(self.url).path or "/"
        parts = [f"{path} - {self.title}"]
        if self.headings:
            parts.append("headings: " + "; ".join(self.headings[:4]))
        if self.fields:
            parts.append("fields: " + ", ".join(self.fields[:8]))
        if self.buttons:
            parts.append("buttons: " + ", ".join(self.buttons[:8]))
        return " | ".join(parts)


@dataclass
class WorkflowResult:
    title: str
    objective: str
    outcome: str = "NOT_RUN"
    steps: int = 0


@dataclass
class ExplorationResult:
    agent: AgentResult
    pages: list[PageInfo] = field(default_factory=list)
    discovered: int = 0
    workflows: list[WorkflowResult] = field(default_factory=list)

    def report_extra(self) -> dict[str, Any]:
        return {
            "exploration": {
                "pages_discovered": self.discovered,
                "pages_visited": len(self.pages),
                "workflows_attempted": sum(w.outcome != "NOT_RUN" for w in self.workflows),
                "workflows_completed": sum(w.outcome == "PASS" for w in self.workflows),
                "pages": [p.__dict__ for p in self.pages],
                "workflows": [w.__dict__ for w in self.workflows],
            }
        }


class Explorer:
    def __init__(
        self,
        *,
        notes: str,
        browser: BrowserSession,
        planner_model: StructuredModel,
        executor_model: StructuredModel,
        settings: Settings,
        budget: SessionBudget,
        test_data: TestData,
        on_event: EventSink,
        confirm: ConfirmFn | None,
        ask_user: AskUserFn | None,
        control: SessionControl,
    ):
        self.notes = notes.strip()
        self.browser = browser
        self.planner_model = planner_model
        self.executor_model = executor_model
        self.settings = settings
        self.budget = budget
        self.test_data = test_data
        self.emit = on_event
        self.confirm = confirm
        self.ask_user = ask_user
        self.control = control
        self.agents: list[TestAgent] = []
        self.crawl_actions: list = []  # navigations while crawling, needed by the detectors

    async def run(self) -> ExplorationResult:
        result = ExplorationResult(agent=AgentResult(outcome="PASS", reason="", steps=[]))
        reason = ""
        try:
            self.crawl_actions.append(await self.browser.navigate(self.browser.scope.target_url))
            reason = await self._log_in_if_needed()
            if not reason:
                result.pages, result.discovered = await self._crawl()
                for workflow in await self._propose(result.pages):
                    result.workflows.append(workflow)
                    if reason := await self._run_workflow(workflow):
                        break  # budget, model or browser problem: later workflows would hit it too
        except BudgetExceeded as e:
            reason = f"stopped: {e}"
        except (ModelError, InvalidModelOutput) as e:
            reason = f"model problem: {e}"
        except BrowserClosedError:
            reason = "the browser was closed"
        result.agent = self._combine(reason)
        return result

    def abort(self, status: StepStatus, reason: str) -> None:
        for agent in self.agents:
            agent.abort(status, reason)

    # ------------------------------------------------------------------ 1. login

    async def _log_in_if_needed(self) -> str:
        """Returns a reason to stop, or "" to go on."""
        await self.browser.observe()
        # Empty objective: any login form counts here, even when the notes hold credentials.
        blocker = detect_blocker(self.browser.last_nodes, "", self.browser.page.url)
        if blocker is None:
            return ""
        if blocker.kind == "login_required" and re.search(r"password|passwd|credentials", self.notes, re.I):
            return await self._run_workflow(WorkflowResult(title="Log in", objective=f"Log in. {self.notes}"))
        if self.ask_user:
            self.emit({"type": "waiting_for_user", "step": None, "kind": blocker.kind, "message": blocker.message})
            if await self.ask_user(blocker.kind, blocker.message):
                return ""
        return f"needs a person ({blocker.kind}) before the site can be explored"

    # ------------------------------------------------------------------ 2. crawl

    async def _crawl(self) -> tuple[list[PageInfo], int]:
        limit = self.settings.agent.explore_max_pages
        start = self.browser.page.url
        queue: deque[str] = deque([start])
        seen = {self._key(start)}
        pages: list[PageInfo] = []

        while queue and len(pages) < limit:
            await self.control.checkpoint()
            self.budget.check_time()
            url = queue.popleft()
            result = await self.browser.navigate(url, from_link=url != start)
            self.crawl_actions.append(result)
            observation = await self.browser.observe()
            info = self._page_info(observation, result.http_status, None if result.ok else result.message)
            pages.append(info)
            self.emit({"type": "page_explored", "url": observation.url, "title": observation.title,
                       "status": result.http_status, "count": len(pages)})

            for link in self._links(observation):
                key = self._key(link)
                if key not in seen:
                    seen.add(key)
                    queue.append(link)
        return pages, len(seen)

    def _links(self, observation: Observation) -> list[str]:
        found = []
        for e in observation.elements:
            if e.role != "link" or not e.url or _DO_NOT_FOLLOW.search(e.name or ""):
                continue
            absolute = urldefrag(urljoin(observation.url, e.url))[0]
            if absolute.startswith("http") and self.browser.scope.allows(absolute):
                found.append(absolute)
        return found

    @staticmethod
    def _key(url: str) -> str:
        """/orders/17 and /orders/42 are the same page type; visit one."""
        parts = urlsplit(url)
        return f"{parts.netloc}{path_template(url)}"

    @staticmethod
    def _page_info(o: Observation, status: int | None, error: str | None) -> PageInfo:
        headings = [line.split('"')[1] for line in o.text.splitlines() if line.strip().startswith('heading "')]
        return PageInfo(
            url=o.url, title=o.title, headings=headings,
            links=sum(e.role == "link" for e in o.elements),
            buttons=[e.name for e in o.elements if e.role == "button" and e.name],
            fields=[e.name for e in o.elements if e.role in ("textbox", "searchbox", "combobox", "spinbutton",
                                                           "checkbox") and e.name],
            status=status, error=error,
        )

    # ------------------------------------------------------------------ 3. workflows

    async def _propose(self, pages: list[PageInfo]) -> list[WorkflowResult]:
        count = self.settings.agent.explore_workflows
        if count == 0 or not pages:
            return []
        site_map = "\n".join(f"- {p.describe()}" for p in pages)
        notes = f"\nNOTES FROM THE USER: {self.notes}" if self.notes else ""
        plan = await self.planner_model.generate(
            WorkflowPlan,
            [Message("system", WORKFLOW_SYSTEM.format(count=count)),
             Message("user", f"SITE: {self.browser.scope.target_url}{notes}\nPAGES:\n{site_map}")],
            purpose="explore",
        )
        workflows = [w for w in plan.workflows if w.objective.strip()][:count]
        self.emit({"type": "workflows_proposed", "workflows": [w.model_dump() for w in workflows]})
        # Each workflow starts from the home page, so it needs the login details too.
        suffix = f" {self.notes}" if self.notes else ""
        return [WorkflowResult(title=w.title, objective=w.objective + suffix) for w in workflows]

    async def _run_workflow(self, workflow: WorkflowResult) -> str:
        """Runs one workflow; returns the reason the session must stop, or ""."""
        self.emit({"type": "workflow_started", "title": workflow.title, "objective": workflow.objective})
        first = max((s.sequence for a in self.agents for s in a.steps), default=0) + 1
        agent = TestAgent(
            objective=workflow.objective, browser=self.browser, planner_model=self.planner_model,
            executor_model=self.executor_model, settings=self.settings, budget=self.budget,
            test_data=self.test_data, on_event=self.emit, confirm=self.confirm, control=self.control,
            ask_user=self.ask_user, first_sequence=first, workflow=workflow.title,
            # The workflow text is the model's; only the user's notes can ground a check.
            stated=self.notes,
        )
        self.agents.append(agent)
        outcome = await agent.run()
        workflow.outcome = outcome.outcome
        workflow.steps = len(outcome.steps)
        self.emit({"type": "workflow_finished", "title": workflow.title, "outcome": outcome.outcome})
        return outcome.reason if outcome.reason.startswith(("stopped:", "model problem:", "the browser")) else ""

    def _combine(self, reason: str) -> AgentResult:
        steps = [s for a in self.agents for s in a.steps]
        actions = sorted([*self.crawl_actions, *(x for a in self.agents for x in a.actions)], key=lambda x: x.sequence)
        statuses = [s.status for s in steps if s.status != StepStatus.REPLANNED]
        if any(s == StepStatus.FAILED for s in statuses):
            outcome = "BUGS_FOUND"
        elif reason.startswith("model problem"):
            outcome = "FAILED"
        else:
            # Exploration "passes" when it ran; unverified workflows are listed, not a failure of the run.
            outcome = "PASS"
        return AgentResult(outcome=outcome, reason=reason, steps=steps, actions=actions)
