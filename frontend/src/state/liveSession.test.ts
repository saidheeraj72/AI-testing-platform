import { describe, expect, it } from "vitest";
import type { AgentEvent } from "../api/types";
import { initialLive, reduceEvent, type LiveSession } from "./liveSession";

let seq = 0;
const ev = (type: string, data: Record<string, unknown> = {}): AgentEvent =>
  ({ seq: ++seq, at: 0, session_id: "s", type, ...data }) as AgentEvent;

const crit = (type: string, extra: Record<string, unknown> = {}) => ({
  type, value: null, within: null, role: null, name: null, method: null, negate: false, grounded: false, ...extra,
});

const run = (...events: AgentEvent[]): LiveSession => events.reduce(reduceEvent, initialLive);

describe("reduceEvent", () => {
  it("tracks the plan and step statuses", () => {
    const s = run(
      ev("plan_created", { steps: [
        { sequence: 1, goal: "Log in", criteria: [crit("url_contains", { value: "/login", negate: true })] },
        { sequence: 2, goal: "Open cart", criteria: [] },
      ] }),
      ev("step_started", { step: 1, goal: "Log in" }),
      ev("action_completed", { step: 1, action: "click", ok: true, target: { role: "button", name: "Sign in" } }),
      ev("step_finished", { step: 1, status: "PASSED", reason: "all checks passed" }),
    );
    expect(s.steps.map((x) => x.status)).toEqual(["PASSED", "PENDING"]);
    expect(s.steps[0].criteria[0].text).toBe("URL NOT contains '/login'");
    expect(s.activity.map((a) => a.kind)).toEqual(["info", "step", "action"]);
    expect(s.activity.find((a) => a.kind === "action")?.text).toBe('click button "Sign in"');
  });

  it("replaces the remaining plan when replanned", () => {
    const s = run(
      ev("plan_created", { steps: [
        { sequence: 1, goal: "A", criteria: [] }, { sequence: 2, goal: "B", criteria: [] },
        { sequence: 3, goal: "C", criteria: [] },
      ] }),
      ev("replanned", { after_step: 2, reason: "stuck", steps: [{ sequence: 3, goal: "B again", criteria: [] }] }),
    );
    expect(s.steps.map((x) => [x.goal, x.status])).toEqual([["A", "PENDING"], ["B", "REPLANNED"], ["B again", "PENDING"]]);
  });

  it("handles confirmation, pause and completion", () => {
    let s = run(ev("confirmation_required", { confirmation_id: "c1", action: "clicking Delete" }));
    expect(s.pendingConfirmation).toEqual({ id: "c1", kind: "risky_action", action: "clicking Delete" });
    s = [ev("confirmation_answered", { allowed: false }), ev("paused")].reduce(reduceEvent, s);
    expect(s.pendingConfirmation).toBeNull();
    expect(s.paused).toBe(true);
    s = [ev("bug_confirmed", { id: "BUG-001", title: "Boom", severity: "high" }),
         ev("session_completed", { outcome: "BUGS_FOUND", reason: "" }), ev("session_saved")].reduce(reduceEvent, s);
    expect(s.bugs).toHaveLength(1);
    expect([s.outcome, s.finished]).toEqual(["BUGS_FOUND", true]);
  });

  it("labels hand-overs differently from risky actions", () => {
    const s = run(
      ev("confirmation_required", { confirmation_id: "c2", kind: "login_required", action: "Log in, then Continue." }),
      ev("confirmation_answered", { allowed: false }),
    );
    expect(s.activity.map((a) => a.text)).toEqual(["Needs you: Log in, then Continue.", "You skipped this step"]);
  });

  it("explains blocked navigation", () => {
    const s = run(ev("action_completed", { action: "click", ok: false, target: null, error: "BLOCKED_NAVIGATION",
                                          message: "outside the test scope" }));
    expect(s.activity[0].detail).toContain("extra allowed domains");
  });

  it("ignores replayed events after a reconnect", () => {
    const first = ev("session_started");
    const s = run(first, ev("paused"));
    expect(reduceEvent(s, first)).toBe(s);
  });
});
