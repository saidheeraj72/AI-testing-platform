import { Link, useParams } from "react-router";
import { useReport, useSession } from "../api/hooks";
import { SeverityBadge } from "../components/Badges";
import { Screenshot } from "../components/Screenshot";
import { pathOf } from "../format";
import { DownloadButton } from "./SessionPage";

export default function BugPage() {
  const { id = "", code = "" } = useParams();
  const session = useSession(id);
  const report = useReport(id, true);
  const bug = session.data?.bugs.find((b) => b.code === code);
  const evidence = report.data?.bugs.find((b) => b.id === code)?.evidence;

  if (session.isLoading) return <p className="empty">Loading…</p>;
  if (!bug) return <div className="error-box">Bug {code} not found in this session.</div>;

  const shots = bug.occurrences.filter((o) => o.screenshot_path);

  return (
    <div className="stack">
      <Link to={`/sessions/${id}`} className="small">← Back to the test</Link>
      <div>
        <div className="row small muted" style={{ marginBottom: "0.3rem" }}>
          <span>{bug.code}</span>
          <SeverityBadge severity={bug.severity} />
          <span>{bug.category}</span>
          <span>· described by {bug.described_by === "analyzer" ? "the AI analyzer" : "rules (no model)"}</span>
        </div>
        <h1>{bug.title}</h1>
        <p className="muted">{bug.summary}</p>
      </div>

      <div className="grid-2">
        <section className="card stack">
          <div>
            <h3>Expected</h3>
            <p>{bug.expected}</p>
          </div>
          <div>
            <h3>Actual</h3>
            <p>{bug.actual}</p>
          </div>
          <div>
            <h3>Where</h3>
            <p><code>{pathOf(bug.url)}</code></p>
          </div>
        </section>
        <section className="card">
          <h3>Steps to reproduce</h3>
          <ol style={{ margin: 0, paddingLeft: "1.2rem" }}>
            {bug.steps_to_reproduce.map((s, i) => <li key={i}>{s}</li>)}
          </ol>
        </section>
      </div>

      {shots.length > 0 && (
        <section className="card">
          <h2>Screenshots</h2>
          <div className="shots">
            {shots.map((o) => (
              <figure key={o.action_seq} style={{ margin: 0 }}>
                <Screenshot sessionId={id} path={o.screenshot_path!} alt={`Page when ${bug.code} occurred`} />
                <figcaption className="small muted">Step {o.step ?? "–"}, action #{o.action_seq}</figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}

      {evidence && evidence.network.length > 0 && (
        <section className="card">
          <h2>Network</h2>
          <table>
            <thead><tr><th>Request</th><th>Result</th><th>Response</th></tr></thead>
            <tbody>
              {evidence.network.map((n, i) => (
                <tr key={i}>
                  <td><code>{n.method} {pathOf(n.url)}</code></td>
                  <td>{n.status ? <span className="badge bad">HTTP {n.status}</span> : <span className="badge bad">{n.failure}</span>}</td>
                  <td>{n.response_body ? <pre>{n.response_body}</pre> : <span className="muted">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {evidence && evidence.console.length > 0 && (
        <section className="card">
          <h2>Console</h2>
          <pre>{evidence.console.join("\n")}</pre>
        </section>
      )}

      <section className="card">
        <h2>Occurrences</h2>
        <table>
          <thead><tr><th>Step</th><th>Action</th><th>Page</th></tr></thead>
          <tbody>
            {bug.occurrences.map((o) => (
              <tr key={o.action_seq}><td>{o.step ?? "–"}</td><td>#{o.action_seq}</td><td><code>{pathOf(o.url)}</code></td></tr>
            ))}
          </tbody>
        </table>
        {evidence?.trace && (
          <p className="small" style={{ marginTop: "0.75rem" }}>
            <DownloadButton sessionId={id} path={evidence.trace} label="Download Playwright trace" link /> to replay
            the whole session.
          </p>
        )}
      </section>
    </div>
  );
}
