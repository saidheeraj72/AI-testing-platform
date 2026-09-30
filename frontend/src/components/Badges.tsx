import type { Bug, StepStatus } from "../api/types";

const STATUS: Record<string, { tone: string; label: string; live?: boolean }> = {
  CREATED: { tone: "", label: "Created" },
  RUNNING: { tone: "info", label: "Running", live: true },
  PAUSED: { tone: "warn", label: "Paused" },
  WAITING_FOR_USER: { tone: "warn", label: "Needs you", live: true },
  COMPLETED: { tone: "", label: "Completed" },
  CANCELLED: { tone: "", label: "Stopped" },
  FAILED: { tone: "bad", label: "Failed" },
  INTERRUPTED: { tone: "warn", label: "Interrupted" },
};

const OUTCOME: Record<string, { tone: string; label: string }> = {
  PASS: { tone: "ok", label: "Passed" },
  BUGS_FOUND: { tone: "bad", label: "Bugs found" },
  COULD_NOT_VERIFY: { tone: "warn", label: "Could not verify" },
  BLOCKED: { tone: "warn", label: "Blocked" },
  FAILED: { tone: "bad", label: "Run failed" },
  CANCELLED: { tone: "", label: "Cancelled" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { tone: "", label: status };
  return (
    <span className={`badge ${s.tone}`}>
      {s.live && <span className="dot pulse" />}
      {s.label}
    </span>
  );
}

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return null;
  const o = OUTCOME[outcome] ?? { tone: "", label: outcome };
  return <span className={`badge ${o.tone}`}>{o.label}</span>;
}

const SEVERITY: Record<Bug["severity"], string> = { critical: "bad", high: "bad", medium: "warn", low: "" };

export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`badge ${SEVERITY[severity as Bug["severity"]] ?? ""}`}>{severity}</span>;
}

const STEP_ICON: Record<StepStatus, [string, string]> = {
  PENDING: ["○", "icon-muted"],
  RUNNING: ["→", "icon-info"],
  PASSED: ["✓", "icon-ok"],
  FAILED: ["✗", "icon-bad"],
  COULD_NOT_VERIFY: ["?", "icon-warn"],
  BLOCKED: ["⊘", "icon-warn"],
  SKIPPED: ["–", "icon-muted"],
  REPLANNED: ["↻", "icon-muted"],
};

export const STEP_LABEL: Record<StepStatus, string> = {
  PENDING: "Pending", RUNNING: "Running", PASSED: "Passed", FAILED: "Failed", COULD_NOT_VERIFY: "Could not verify",
  BLOCKED: "Blocked", SKIPPED: "Skipped", REPLANNED: "Replaced by a new plan",
};

export function StepIcon({ status }: { status: StepStatus }) {
  const [icon, cls] = STEP_ICON[status] ?? ["·", "icon-muted"];
  return (
    <span className={`step-icon ${cls}`} title={STEP_LABEL[status]} aria-label={STEP_LABEL[status]}>
      {icon}
    </span>
  );
}
