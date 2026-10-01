import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { useCreateProject, useCreateSession, useLoginSetup, useProjects, useSessions, useUpdateProject } from "../api/hooks";
import type { Project } from "../api/types";
import { OutcomeBadge, StatusBadge } from "../components/Badges";

const normalize = (url: string) => url.trim().replace(/\/+$/, "");

export default function NewTest() {
  const navigate = useNavigate();
  const projects = useProjects();
  const createProject = useCreateProject();
  const createSession = useCreateSession();

  const [url, setUrl] = useState("");
  const [objective, setObjective] = useState("");
  const [name, setName] = useState("");
  const [keepLogin, setKeepLogin] = useState(true);
  const [extraDomains, setExtraDomains] = useState("");
  const [error, setError] = useState<string | null>(null);

  // A project is the target site plus its browser profile; reuse it when the URL matches.
  const existing = useMemo<Project | undefined>(
    () => projects.data?.find((p) => normalize(p.target_url) === normalize(url)),
    [projects.data, url],
  );
  const busy = createProject.isPending || createSession.isPending;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const project =
        existing ??
        (await createProject.mutateAsync({
          name: name.trim() || new URL(url).host,
          target_url: url.trim(),
          allowed_domains: extraDomains.split(/[\s,]+/).filter(Boolean),
          persistent_profile: keepLogin,
        }));
      const session = await createSession.mutateAsync({ project_id: project.id, objective: objective.trim() });
      navigate(`/sessions/${session.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="stack">
      <div>
        <h1>New test</h1>
        <p className="muted">
          The agent tests the website in a real Chrome window on your desktop and reports only bugs it has evidence for.
        </p>
      </div>
      <form className="card stack" onSubmit={submit}>
        <label>
          Website
          <input type="url" required placeholder="https://staging.example.com" value={url}
                 onChange={(e) => setUrl(e.target.value)} />
          <span className="hint">
            The agent stays on this site (and its subdomains). For localhost, the port must match.
          </span>
        </label>
        <label>
          What should I test?
          <textarea required minLength={3} rows={4} value={objective} onChange={(e) => setObjective(e.target.value)}
                    placeholder='Log in with demo@example.com / password123. Create a customer and verify it appears in the customer list.' />
          <span className="hint">
            State exact expectations to have mismatches reported as bugs: quote texts ("Order confirmed") and give
            paths (/login). Vaguer expectations are still checked, but a mismatch is reported as "could not verify".
          </span>
        </label>

        {url && (existing ? (
          <ExistingProject project={existing} />
        ) : (
          <details>
            <summary>New project options</summary>
            <div className="stack" style={{ marginTop: "0.75rem" }}>
              <label>
                Project name
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="CRM staging" />
              </label>
              <label className="inline">
                <input type="checkbox" checked={keepLogin} onChange={(e) => setKeepLogin(e.target.checked)} />
                Keep the browser login between tests (log in once by hand, pausing the first test)
              </label>
              <label>
                Extra allowed domains
                <input value={extraDomains} onChange={(e) => setExtraDomains(e.target.value)}
                       placeholder="login.microsoftonline.com" />
                <span className="hint">For example a single sign-on provider. Separate with spaces or commas.</span>
              </label>
            </div>
          </details>
        ))}

        {error && <div className="error-box" role="alert">{error}</div>}
        <div>
          <button type="submit" disabled={busy}>{busy ? "Starting…" : "Start testing"}</button>
        </div>
      </form>
      <RecentSessions />
    </div>
  );
}

function ExistingProject({ project }: { project: Project }) {
  const login = useLoginSetup(project.persistent_profile ? project.id : undefined);
  const update = useUpdateProject();
  const [domains, setDomains] = useState(project.allowed_domains.join(", "));
  return (
    <div className="stack">
      <div className="row small muted">
        <span>Project: <strong>{project.name}</strong></span>
        {project.persistent_profile && <span>· keeps its login between tests</span>}
        {project.persistent_profile && !login.open && (
          <button type="button" className="link" onClick={() => login.action.mutate("open")}
                  disabled={login.action.isPending}>Set up login</button>
        )}
      </div>
      {login.open && (
        <div className="banner info" role="status">
          <div><strong>A Chrome window is open for this project.</strong> Log in there, then click Done. Later tests
            start logged in.</div>
          <button type="button" onClick={() => login.action.mutate("finish")} disabled={login.action.isPending}>Done</button>
        </div>
      )}
      {login.action.isError && <div className="error-box">{login.action.error.message}</div>}
      <details>
        <summary className="small">Extra allowed domains</summary>
        <div className="row" style={{ marginTop: "0.5rem" }}>
          <input style={{ flex: 1 }} value={domains} onChange={(e) => setDomains(e.target.value)}
                 placeholder="login.microsoftonline.com" aria-label="Extra allowed domains" />
          <button type="button" className="secondary" disabled={update.isPending}
                  onClick={() => update.mutate({ id: project.id, allowed_domains: domains.split(/[\s,]+/).filter(Boolean) })}>
            Save
          </button>
        </div>
        <p className="hint">Sites the agent may visit besides the target, such as a single sign-on provider.</p>
      </details>
    </div>
  );
}

function RecentSessions() {
  const sessions = useSessions();
  const navigate = useNavigate();
  const recent = sessions.data?.slice(0, 5) ?? [];
  if (recent.length === 0) return null;
  return (
    <section className="card">
      <div className="spread">
        <h2>Recent tests</h2>
        <Link to="/history" className="small">All tests</Link>
      </div>
      <table>
        <tbody>
          {recent.map((s) => (
            <tr key={s.id} className="clickable" onClick={() => navigate(`/sessions/${s.id}`)}>
              <td>{s.objective.length > 90 ? `${s.objective.slice(0, 90)}…` : s.objective}</td>
              <td><StatusBadge status={s.status} /></td>
              <td>{s.status === "COMPLETED" && <OutcomeBadge outcome={s.outcome} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
