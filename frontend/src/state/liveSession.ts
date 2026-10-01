// Folds the session's event stream (WebSocket, replayed from the start) into the running view.
import type { AgentEvent, Criterion, Pending, StepStatus } from "../api/types";
import { describeCriterion } from "./criteria";

export interface LiveCheck {
  check: string;
  passed: boolean;
  detail: string;
}

export interface LiveStep {
  sequence: number;
  goal: string;
  criteria: { text: string; grounded: boolean }[];
  status: StepStatus;
  reason: string;
  checks: LiveCheck[];
}

export type ActivityKind = "step" | "thought" | "action" | "replan" | "bug" | "confirm" | "info" | "error";

export interface Activity {
  seq: number;
  at: number;
  kind: ActivityKind;
  text: string;
  ok?: boolean;
  detail?: string;
}

export interface LiveSession {
  steps: LiveStep[];
  activity: Activity[];
  pendingConfirmation: Pending | null;
  paused: boolean;
  bugs: { id: string; title: string; severity: string }[];
  analyzing: boolean;
  outcome: string | null;
  finished: boolean;
  lastSeq: number;
}

export const initialLive: LiveSession = {
  steps: [],
  activity: [],
  pendingConfirmation: null,
  paused: false,
  bugs: [],
  analyzing: false,
  outcome: null,
  finished: false,
  lastSeq: 0,
};

interface PlannedStep {
  sequence: number;
  goal: string;
  criteria: Criterion[];
}

const toStep = (s: PlannedStep): LiveStep => ({
  sequence: s.sequence,
  goal: s.goal,
  criteria: s.criteria.map((c) => ({ text: describeCriterion(c), grounded: c.grounded })),
  status: "PENDING",
  reason: "",
  checks: [],
});

export function reduceEvent(state: LiveSession, e: AgentEvent): LiveSession {
  if (e.seq && e.seq <= state.lastSeq) return state; // replay after reconnect
  const s: LiveSession = { ...state, lastSeq: e.seq || state.lastSeq };
  const log = (kind: ActivityKind, text: string, extra: Partial<Activity> = {}) => {
    s.activity = [...s.activity, { seq: e.seq, at: e.at, kind, text, ...extra }];
  };
  const updateStep = (sequence: number, patch: Partial<LiveStep>) => {
    s.steps = s.steps.map((st) => (st.sequence === sequence ? { ...st, ...patch } : st));
  };

  switch (e.type) {
    case "session_started":
      log("info", "Session started");
      break;
    case "plan_created":
      s.steps = (e.steps as PlannedStep[]).map(toStep);
      log("info", `Plan created: ${s.steps.length} steps`);
      break;
    case "replanned": {
      const after = e.after_step as number;
      const kept = s.steps.filter((st) => st.sequence <= after);
      s.steps = [...kept, ...(e.steps as PlannedStep[]).map(toStep)];
      updateStep(after, { status: "REPLANNED", reason: e.reason as string });
      log("replan", `Replanned after step ${after}`, { detail: e.reason as string });
      break;
    }
    case "step_started":
      updateStep(e.step as number, { status: "RUNNING" });
      log("step", `Step ${e.step}: ${e.goal}`);
      break;
    case "decision":
      if (e.action !== "verify") log("thought", e.reasoning as string);
      break;
    case "action_completed": {
      const target = e.target as { role: string; name: string } | null;
      const text = `${e.action}${target ? ` ${target.role} "${target.name}"` : ""}`;
      let detail = e.ok ? undefined : `${e.error}: ${e.message}`;
      if (e.error === "BLOCKED_NAVIGATION") {
        detail += " If this site is part of the login (single sign-on), add it to the project's extra allowed domains.";
      }
      log("action", text, { ok: e.ok as boolean, detail });
      break;
    }
    case "checks":
      updateStep(e.step as number, { checks: e.results as LiveCheck[] });
      break;
    case "step_finished":
      updateStep(e.step as number, { status: e.status as StepStatus, reason: e.reason as string });
      break;
    case "confirmation_required": {
      const kind = (e.kind as string) ?? "risky_action";
      s.pendingConfirmation = { id: e.confirmation_id as string, kind, action: e.action as string };
      log("confirm", kind === "risky_action" ? `Waiting for your approval: ${e.action}` : `Needs you: ${e.action}`);
      break;
    }
    case "confirmation_answered": {
      const risky = (s.pendingConfirmation?.kind ?? "risky_action") === "risky_action";
      s.pendingConfirmation = null;
      const text = risky ? (e.allowed ? "You allowed the action" : "You refused the action")
                         : (e.allowed ? "You continued the test" : "You skipped this step");
      log("confirm", text, { ok: e.allowed as boolean });
      break;
    }
    case "page_explored": {
      const status = e.status as number | null;
      log("action", `Explored ${pathOnly(e.url as string)}: ${e.title}`, {
        ok: status === null || status < 400,
        detail: status !== null && status >= 400 ? `HTTP ${status}` : undefined,
      });
      break;
    }
    case "workflows_proposed":
      log("info", `Workflows to test: ${(e.workflows as { title: string }[]).map((w) => w.title).join(", ") || "none"}`);
      break;
    case "workflow_started":
      log("step", `Workflow: ${e.title}`, { detail: e.objective as string });
      break;
    case "workflow_finished":
      log("info", `Workflow "${e.title}" finished: ${e.outcome}`);
      break;
    case "paused":
      s.paused = true;
      log("info", "Paused. You have control of the browser.");
      break;
    case "resumed":
      s.paused = false;
      log("info", "Resumed. The agent re-reads the page before continuing.");
      break;
    case "bug_candidate":
      s.analyzing = true;
      break;
    case "bug_confirmed":
      s.bugs = [...s.bugs, { id: e.id as string, title: e.title as string, severity: e.severity as string }];
      log("bug", `${e.id}: ${e.title}`, { detail: e.severity as string });
      break;
    case "session_completed":
      s.outcome = e.outcome as string;
      s.analyzing = false;
      log("info", `Finished: ${e.outcome}`, { detail: (e.reason as string) || undefined });
      break;
    case "session_failed":
      log("error", `Session failed: ${e.error}`);
      break;
    case "session_saved":
    case "stream_end":
      s.finished = true;
      s.pendingConfirmation = null;
      break;
  }
  return s;
}

function pathOnly(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return url;
  }
}
