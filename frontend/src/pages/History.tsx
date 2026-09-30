import { useNavigate } from "react-router";
import { useProjects, useSessions } from "../api/hooks";
import { OutcomeBadge, StatusBadge } from "../components/Badges";
import { duration, when } from "../format";

export default function History() {
  const sessions = useSessions();
  const projects = useProjects();
  const navigate = useNavigate();
  const projectName = (id: string) => projects.data?.find((p) => p.id === id)?.name ?? id;

  return (
    <div className="stack">
      <h1>History</h1>
      <section className="card">
        {sessions.isLoading ? (
          <p className="empty">Loading…</p>
        ) : !sessions.data?.length ? (
          <p className="empty">No tests yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Test</th><th>Project</th><th>Status</th><th>Result</th><th>Steps</th><th>Bugs</th><th>Duration</th><th>Started</th>
              </tr>
            </thead>
            <tbody>
              {sessions.data.map((s) => (
                <tr key={s.id} className="clickable" onClick={() => navigate(`/sessions/${s.id}`)}>
                  <td>{s.objective.length > 70 ? `${s.objective.slice(0, 70)}…` : s.objective}</td>
                  <td className="muted">{projectName(s.project_id)}</td>
                  <td><StatusBadge status={s.status} /></td>
                  <td>{s.status === "COMPLETED" && <OutcomeBadge outcome={s.outcome} />}</td>
                  <td>{s.steps_completed} / {s.steps_planned}</td>
                  <td>{s.bugs_found || "–"}</td>
                  <td>{duration(s.duration_ms)}</td>
                  <td className="muted small">{when(s.started_at ?? s.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
