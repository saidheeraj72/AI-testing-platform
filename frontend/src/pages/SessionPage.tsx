import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api, fetchFile } from "../api/client";
import { isActive, useConfirm, useEvidence, useProjects, useReport, useSession, useSessionControl } from "../api/hooks";
import type { AgentEvent, Exploration, Pending, Report, SessionDetail, Step } from "../api/types";
import { ActivityFeed } from "../components/ActivityFeed";
import { OutcomeBadge, SeverityBadge, StatusBadge } from "../components/Badges";
import { StepList, type StepView } from "../components/StepList";
import { duration, pathOf } from "../format";
import { describeCriterion } from "../state/criteria";
import { initialLive, reduceEvent, type LiveSession } from "../state/liveSession";
import { useLiveSession } from "../state/useLiveSession";

export default function SessionPage() {
  const { id = "" } = useParams();
  const session = useSession(id);
  const queryClient = useQueryClient();
  const s = session.data;
  const active = isActive(s);
  const live = useLiveSession(id, active);

  // When the stream ends the results are in the database: reload them.
  useEffect(() => {
    if (live.finished) queryClient.invalidateQueries({ queryKey: ["session", id] });
  }, [live.finished, id, queryClient]);

  if (session.isLoading) return <p className="empty">Loading…</p>;
  if (session.isError || !s) return <div className="error-box">{session.error?.message ?? "Session not found"}</div>;

  return (
    <div className="stack">
      <Header session={s} />
      {s.status === "CREATED" ? (
        <StartCreated sessionId={id} />
      ) : active ? (
        <LiveView session={s} live={live} />
      ) : (
        <Results session={s} />
      )}
    </div>
  );
}

function Header({ session: s }: { session: SessionDetail }) {
  const projects = useProjects();
  const project = projects.data?.find((p) => p.id === s.project_id);
  return (
    <div className="spread">
      <div>
        <div className="row small muted" style={{ marginBottom: "0.3rem" }}>
          {project && <span>{project.name} · {project.target_url}</span>}
          {s.model && <span>· {s.model}</span>}
        </div>
        <h1>{s.objective}</h1>
      </div>
      <div className="row">
        <StatusBadge status={s.status} />
        {s.status === "COMPLETED" && <OutcomeBadge outcome={s.outcome} />}
      </div>
    </div>
  );
}

function StartCreated({ sessionId }: { sessionId: string }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const start = () =>
    api(`/api/sessions/${sessionId}/start`, { method: "POST" })
      .then(() => queryClient.invalidateQueries({ queryKey: ["session", sessionId] }))
      .catch((e: Error) => setError(e.message));
  return (
    <div className="card stack">
      <p>This test has not started yet.</p>
      {error && <div className="error-box">{error}</div>}
      <div><button onClick={start}>Start testing</button></div>
    </div>
  );
}

// ------------------------------------------------------------------ running

function LiveView({ session: s, live }: { session: SessionDetail; live: LiveSession }) {
  const control = useSessionControl(s.id);
  const status = s.live?.status ?? s.status;
  const pending = live.pendingConfirmation ?? s.live?.pending_confirmation ?? null;
  const elapsed = useElapsed(s.started_at);
  const steps: StepView[] = live.steps.length ? live.steps : [];

  const stop = () => {
    if (window.confirm("Stop this test? The report and trace recorded so far are kept.")) control.mutate("stop");
  };

  return (
    <>
      {pending && <ConfirmBanner sessionId={s.id} pending={pending} />}
      {status === "PAUSED" && (
        <div className="banner warn" role="status">
          <div>
            <strong>You have control of the browser.</strong> Use the Chrome window as you like, for example to log in
            or fix the page. The agent reads the page again when it resumes.
          </div>
          <button onClick={() => control.mutate("resume")} disabled={control.isPending}>Resume AI</button>
        </div>
      )}

      <div className="card">
        <div className="spread">
          <div className="stats" style={{ flex: 1 }}>
            <Stat label="Steps" value={`${steps.filter((x) => x.status === "PASSED").length} / ${steps.filter((x) => x.status !== "REPLANNED").length || "…"}`} />
            <Stat label="Actions" value={String(s.live?.actions ?? live.activity.filter((a) => a.kind === "action").length)} />
            <Stat label="Bugs" value={String(live.bugs.length)} />
            {s.mode === "explore" && <Stat label="Pages explored" value={String(s.live?.pages_explored ?? 0)} />}
            <Stat label="Elapsed" value={elapsed} />
          </div>
          <div className="row">
            {status === "RUNNING" && (
              <button className="secondary" onClick={() => control.mutate("pause")} disabled={control.isPending}>
                Take control
              </button>
            )}
            <button className="danger" onClick={stop} disabled={control.isPending}>Stop</button>
          </div>
        </div>
        <p className="small muted" style={{ margin: "0.75rem 0 0" }}>
          The test runs in a Chrome window on your desktop. Clicking in that window while the agent is working can
          confuse it; use Take control first.
          {live.analyzing && " Analysing what was found…"}
        </p>
        {control.isError && <div className="error-box" style={{ marginTop: "0.5rem" }}>{control.error.message}</div>}
      </div>

      <div className="grid-2">
        <section className="card">
          <h2>Plan</h2>
          <StepList steps={steps} />
        </section>
        <section className="card">
          <h2>Activity</h2>
          <ActivityFeed items={live.activity} />
        </section>
      </div>
    </>
  );
}

const HAND_OVER_TITLE: Record<string, string> = {
  login_required: "Please log in.",
  mfa: "A verification code is needed.",
  captcha: "A CAPTCHA needs a person.",
  agent_request: "The agent asks for your help.",
};

function ConfirmBanner({ sessionId, pending }: { sessionId: string; pending: Pending }) {
  const confirm = useConfirm(sessionId);
  const answer = (allow: boolean) => confirm.mutate({ confirmation_id: pending.id, allow });
  if (pending.kind !== "risky_action") {
    return (
      <div className="banner warn" role="alert">
        <div>
          <strong>{HAND_OVER_TITLE[pending.kind] ?? "The agent needs you."}</strong> {pending.action}
          <div className="small">The agent waits and does not touch the browser. It reads the page again when you
            continue.</div>
        </div>
        <div className="row">
          <button onClick={() => answer(true)} disabled={confirm.isPending}>Continue</button>
          <button className="secondary" onClick={() => answer(false)} disabled={confirm.isPending}>Skip this step</button>
        </div>
      </div>
    );
  }
  return (
    <div className="banner warn" role="alert">
      <div>
        <strong>The agent wants to do something that may be irreversible:</strong> {pending.action}.
        <div className="small">Refusing ends this step as blocked; the test does not do it.</div>
      </div>
      <div className="row">
        <button onClick={() => answer(true)} disabled={confirm.isPending}>Allow once</button>
        <button className="secondary" onClick={() => answer(false)} disabled={confirm.isPending}>Refuse</button>
      </div>
    </div>
  );
}

function useElapsed(startedAt: string | null): string {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return startedAt ? duration(now - new Date(startedAt).getTime()) : "–";
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

// ------------------------------------------------------------------ results

function Results({ session: s }: { session: SessionDetail }) {
  const report = useReport(s.id, true);
  const r = report.data;
  const steps = s.steps.map(stepView);

  return (
    <>
      <section className="card stack">
        {s.reason && <p className="muted">{s.reason}</p>}
        <div className="stats">
          <Stat label="Duration" value={duration(s.duration_ms)} />
          <Stat label="Steps passed" value={`${s.steps_completed} / ${s.steps_planned}`} />
          <Stat label="Could not verify" value={String(s.steps_could_not_verify)} />
          <Stat label="Not tested" value={String(s.steps_skipped)} />
          <Stat label="Pages visited" value={String(s.pages_visited)} />
          <Stat label="Bugs" value={String(s.bugs_found)} />
        </div>
      </section>

      <section className="card">
        <h2>Bugs</h2>
        {s.bugs.length === 0 ? (
          <p className="empty">No bugs found{s.outcome === "PASS" ? "; every step passed its checks." : "."}</p>
        ) : (
          <div className="stack">
            {s.bugs.map((b) => (
              <Link key={b.code} to={`/sessions/${s.id}/bugs/${b.code}`} className="bug-card">
                <div className="row">
                  <SeverityBadge severity={b.severity} />
                  <strong>{b.title}</strong>
                </div>
                <span className="small muted">
                  {b.code} · {b.category} · {b.occurrences.length} occurrence{b.occurrences.length === 1 ? "" : "s"}
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>

      {r?.exploration && <ExplorationSection exploration={r.exploration} />}

      {r && (r.could_not_verify.length > 0 || r.not_tested.length > 0) && <NotVerified report={r} />}

      <div className="grid-2">
        <section className="card">
          <h2>Plan and checks</h2>
          <StepList steps={steps} />
        </section>
        <section className="card stack">
          <Timeline sessionId={s.id} />
          <Evidence sessionId={s.id} report={r} />
        </section>
      </div>
    </>
  );
}

function stepView(step: Step): StepView {
  return {
    sequence: step.sequence,
    goal: step.goal,
    status: step.status,
    reason: step.reason,
    criteria: step.success_criteria.map((c) => ({ text: describeCriterion(c), grounded: c.grounded })),
    checks: step.checks.map((c) => ({ check: describeCriterion(c.criterion), passed: c.passed, detail: c.detail })),
  };
}

function ExplorationSection({ exploration: x }: { exploration: Exploration }) {
  return (
    <section className="card stack">
      <h2>Exploration</h2>
      <div className="stats">
        <Stat label="Pages visited" value={`${x.pages_visited} of ${x.pages_discovered} found`} />
        <Stat label="Workflows completed" value={`${x.workflows_completed} / ${x.workflows_attempted}`} />
      </div>
      {x.workflows.length > 0 && (
        <table>
          <thead><tr><th>Workflow</th><th>Result</th></tr></thead>
          <tbody>
            {x.workflows.map((w) => (
              <tr key={w.title}>
                <td><strong>{w.title}</strong><div className="small muted">{w.objective}</div></td>
                <td>{w.outcome === "NOT_RUN" ? <span className="badge">Not run</span> : <OutcomeBadge outcome={w.outcome} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <details>
        <summary>Pages ({x.pages.length})</summary>
        <table style={{ marginTop: "0.5rem" }}>
          <tbody>
            {x.pages.map((p) => (
              <tr key={p.url}>
                <td><code>{pathOf(p.url)}</code></td>
                <td>{p.title}</td>
                <td>{p.status && p.status >= 400 ? <span className="badge bad">HTTP {p.status}</span> : <span className="muted small">{p.status ?? ""}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

function NotVerified({ report }: { report: Report }) {
  return (
    <section className="card">
      <h2>Not verified</h2>
      <table>
        <tbody>
          {report.could_not_verify.map((x) => (
            <tr key={`c${x.step}`}>
              <td><span className="badge warn">Could not verify</span></td>
              <td>{x.goal}<div className="small muted">{x.reason}</div></td>
            </tr>
          ))}
          {report.not_tested.map((x) => (
            <tr key={`n${x.step}`}>
              <td><span className="badge">{x.status === "BLOCKED" ? "Blocked" : "Not tested"}</span></td>
              <td>{x.goal}<div className="small muted">{x.reason}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
      {report.unconfirmed.length > 0 && (
        <details style={{ marginTop: "0.75rem" }}>
          <summary>{report.unconfirmed.length} unconfirmed signal(s)</summary>
          <p className="small muted">Weak signals the analyzer did not confirm as bugs.</p>
          <ul className="small">
            {report.unconfirmed.map((u, i) => <li key={i}>{u.signals.join("; ")} <span className="muted">({u.url})</span></li>)}
          </ul>
        </details>
      )}
    </section>
  );
}

function Timeline({ sessionId }: { sessionId: string }) {
  const events = useQuery({
    queryKey: ["events", sessionId],
    queryFn: () => api<AgentEvent[]>(`/api/sessions/${sessionId}/events`),
  });
  const activity = (events.data ?? []).reduce(reduceEvent, initialLive).activity;
  return (
    <details open={activity.length > 0 && activity.length < 60}>
      <summary>Timeline</summary>
      <div style={{ marginTop: "0.5rem" }}>
        {activity.length ? <ActivityFeed items={activity} /> : (
          <p className="empty small">The live timeline is kept while the server runs. The full record is in the
            evidence files below.</p>
        )}
      </div>
    </details>
  );
}

function Evidence({ sessionId, report }: { sessionId: string; report: Report | undefined }) {
  const files = useEvidence(sessionId, true);
  const count = files.data?.length ?? 0;
  return (
    <details>
      <summary>Evidence ({count} files)</summary>
      <div className="stack" style={{ marginTop: "0.5rem" }}>
        {report?.trace && (
          <div>
            <DownloadButton sessionId={sessionId} path={report.trace} label="Download Playwright trace" />
            <p className="small muted">Open it with <code>npx playwright show-trace trace.zip</code> to replay every
              action with screenshots, network and console.</p>
          </div>
        )}
        <table>
          <tbody>
            {(files.data ?? []).map((f) => (
              <tr key={f.path}>
                <td><code>{f.path}</code></td>
                <td className="muted small">{f.type}</td>
                <td><DownloadButton sessionId={sessionId} path={f.path} label="Download" link /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export function DownloadButton({ sessionId, path, label, link }: { sessionId: string; path: string; label: string; link?: boolean }) {
  const download = async () => {
    const blob = await fetchFile(sessionId, path);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = path.split("/").pop() ?? "file";
    a.click();
    URL.revokeObjectURL(url);
  };
  return <button className={link ? "link" : "secondary"} onClick={download}>{label}</button>;
}
