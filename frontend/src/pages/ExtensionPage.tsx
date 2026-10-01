import { useState } from "react";
import { API_URL, apiToken } from "../api/client";

/** How to install the Chrome extension and pair it with this AI Tester. */
export default function ExtensionPage() {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(apiToken());
    setCopied(true);
  };
  return (
    <div className="stack">
      <div>
        <h1>Chrome extension</h1>
        <p className="muted">
          Test the tab you already have open, with your own logins and cookies. The agent, checks and bug reports are
          the same as for tests in AI Tester's own browser.
        </p>
      </div>
      <section className="card stack">
        <h2>Set up (once)</h2>
        <ol style={{ margin: 0, paddingLeft: "1.2rem", display: "grid", gap: "0.4rem" }}>
          <li>Open <code>chrome://extensions</code> and switch on <strong>Developer mode</strong>.</li>
          <li>Click <strong>Load unpacked</strong> and choose the <code>extension</code> folder of this project.</li>
          <li>Open the extension's <strong>Settings</strong> (from its popup) and enter:</li>
        </ol>
        <div className="stack" style={{ maxWidth: 560 }}>
          <label>
            API address
            <input readOnly value={API_URL} onFocus={(e) => e.target.select()} />
          </label>
          <label>
            API token
            <div className="row">
              <input readOnly type="password" value={apiToken()} style={{ flex: 1 }} aria-label="API token" />
              <button type="button" className="secondary" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            </div>
            <span className="hint">The token changes each time AI Tester starts, so paste it again after a restart.
              Treat it like a password: it lets the extension control this AI Tester.</span>
          </label>
        </div>
      </section>
      <section className="card stack">
        <h2>Test a tab</h2>
        <p>Click the AI Tester icon on any web page, describe what to test (or choose Explore), and click
          <strong> Test this tab</strong>. Follow the test here under History. Chrome shows a bar saying the tab is
          being debugged while the test runs; closing that bar stops the test.</p>
        <p className="small muted">The agent stays on the tab's site. Risky actions still ask for your approval, here
          in AI Tester.</p>
      </section>
    </div>
  );
}
