// Mirrors backend/app/schemas/api.py and the event types emitted by the agent.

export type SessionStatus =
  | "CREATED" | "RUNNING" | "PAUSED" | "WAITING_FOR_USER"
  | "COMPLETED" | "CANCELLED" | "FAILED" | "INTERRUPTED";
export type Outcome = "PASS" | "BUGS_FOUND" | "COULD_NOT_VERIFY" | "BLOCKED" | "FAILED" | "CANCELLED";
export type StepStatus =
  | "PENDING" | "RUNNING" | "PASSED" | "FAILED" | "COULD_NOT_VERIFY" | "BLOCKED" | "SKIPPED" | "REPLANNED";

export interface Project {
  id: string;
  name: string;
  target_url: string;
  allowed_domains: string[];
  persistent_profile: boolean;
  created_at: string;
}

export interface Criterion {
  type: string;
  value: string | null;
  within: string | null;
  role: string | null;
  name: string | null;
  method: string | null;
  negate: boolean;
  grounded: boolean;
}

export interface CheckResult {
  criterion: Criterion;
  passed: boolean;
  detail: string;
  app_error: boolean;
  inconclusive: boolean;
}

export interface LiveInfo {
  status: SessionStatus;
  steps_planned: number;
  steps_completed: number;
  current_step: { sequence: number; goal: string } | null;
  actions: number;
  bugs_found: number;
  pages_explored?: number;
  pending_confirmation: Pending | null;
}

export interface Session {
  id: string;
  project_id: string;
  objective: string;
  mode: "objective" | "explore";
  browser: "managed" | "tab";
  status: SessionStatus;
  outcome: Outcome | null;
  reason: string | null;
  model: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  steps_planned: number;
  steps_completed: number;
  steps_failed: number;
  steps_could_not_verify: number;
  steps_skipped: number;
  pages_visited: number;
  bugs_found: number;
  live: LiveInfo | null;
}

export interface Step {
  sequence: number;
  goal: string;
  status: StepStatus;
  reason: string | null;
  success_criteria: Criterion[];
  checks: CheckResult[];
}

export interface Occurrence {
  step: number | null;
  action_seq: number;
  url: string;
  screenshot_path: string | null;
}

export interface Bug {
  code: string;
  title: string;
  severity: "critical" | "high" | "medium" | "low";
  category: string;
  summary: string;
  expected: string;
  actual: string;
  url: string;
  described_by: "analyzer" | "rules";
  steps_to_reproduce: string[];
  status: string;
  occurrences: Occurrence[];
}

export interface SessionDetail extends Session {
  steps: Step[];
  bugs: Bug[];
}

export interface NetworkEvidence {
  method: string;
  url: string;
  status: number | null;
  failure: string | null;
  response_body: string | null;
}

export interface ReportBug {
  id: string;
  evidence: { network: NetworkEvidence[]; console: string[]; screenshots: string[]; trace: string | null };
}

export interface Report {
  outcome: Outcome;
  reason: string;
  summary: Record<string, number>;
  bugs: ReportBug[];
  unconfirmed: { step: number | null; url: string; action: string | null; signals: string[] }[];
  could_not_verify: { step: number; goal: string; reason: string }[];
  not_tested: { step: number; goal: string; status: string; reason: string }[];
  trace: string | null;
  test_data?: Record<string, string>;
  exploration?: Exploration;
}

export interface Exploration {
  pages_discovered: number;
  pages_visited: number;
  workflows_attempted: number;
  workflows_completed: number;
  pages: { url: string; title: string; status: number | null; error: string | null; fields: string[]; buttons: string[] }[];
  workflows: { title: string; objective: string; outcome: string; steps: number }[];
}

export interface EvidenceFile {
  path: string;
  type: string;
  size: number;
}

export interface SystemInfo {
  model: { provider: string; planner: string; executor: string; analyzer: string };
  browser: { headless: boolean; channel: string };
  safety: { risky_actions: string };
  running_sessions: string[];
  max_concurrent_sessions: number;
}

export interface AgentEvent {
  seq: number;
  at: number;
  session_id: string;
  type: string;
  [key: string]: unknown;
}

/** Something the user must answer: a risky action, or a hand-over (login, MFA, CAPTCHA, agent request). */
export interface Pending {
  id: string;
  kind: "risky_action" | "login_required" | "mfa" | "captcha" | "agent_request" | string;
  action: string;
}
