import type { StepStatus } from "../api/types";
import { StepIcon, STEP_LABEL } from "./Badges";

export interface StepView {
  sequence: number;
  goal: string;
  status: StepStatus;
  reason?: string | null;
  criteria: { text: string; grounded: boolean }[];
  checks?: { check: string; passed: boolean; detail: string }[];
}

/** The plan with each step's success criteria (✓/✗ once checked) and why it ended. */
export function StepList({ steps }: { steps: StepView[] }) {
  if (steps.length === 0) return <p className="empty">Planning…</p>;
  return (
    <ol className="steps">
      {steps.map((step) => {
        const results = new Map((step.checks ?? []).map((c) => [c.check, c]));
        const showReason = step.reason && !["PASSED", "RUNNING", "PENDING"].includes(step.status);
        return (
          <li key={`${step.sequence}-${step.goal}`} className={`step ${step.status === "RUNNING" ? "current" : ""}`}>
            <div className="step-head">
              <StepIcon status={step.status} />
              <div>
                <strong>{step.goal}</strong>{" "}
                {step.status !== "PENDING" && step.status !== "RUNNING" && (
                  <span className="muted small">· {STEP_LABEL[step.status]}</span>
                )}
              </div>
            </div>
            {step.criteria.length > 0 ? (
              <ul className="criteria">
                {step.criteria.map((c) => {
                  const r = results.get(c.text);
                  return (
                    <li key={c.text} title={r?.detail}>
                      {r ? (r.passed ? <span className="icon-ok">✓</span> : <span className="icon-bad">✗</span>) : "·"}{" "}
                      {c.text}
                      {!c.grounded && <span className="muted"> (guess)</span>}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <ul className="criteria"><li>No automatic check for this step</li></ul>
            )}
            {showReason && <p className="reason">{step.reason}</p>}
          </li>
        );
      })}
    </ol>
  );
}
