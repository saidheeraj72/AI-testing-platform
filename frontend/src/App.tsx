import { NavLink, Route, Routes, useLocation } from "react-router";
import { API_URL, hasToken } from "./api/client";
import { useSystemInfo } from "./api/hooks";
import { ErrorBoundary } from "./components/ErrorBoundary";
import BugPage from "./pages/BugPage";
import ExtensionPage from "./pages/ExtensionPage";
import History from "./pages/History";
import NewTest from "./pages/NewTest";
import SessionPage from "./pages/SessionPage";

export default function App() {
  const system = useSystemInfo();
  const location = useLocation();
  return (
    <>
      <header className="topbar">
        <span className="brand">AI Tester</span>
        <nav aria-label="Main">
          <NavLink to="/" end>New test</NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/extension">Chrome extension</NavLink>
        </nav>
        {system.data && <span className="model-chip">Model: {system.data.model.executor}</span>}
      </header>
      <main>
        {(!hasToken || system.isError) && (
          <div className="error-box" role="alert" style={{ marginBottom: "1rem" }}>
            {!hasToken
              ? "No API token configured. Start the app with: uv run python scripts/dev.py"
              : `${system.error?.message} Start it with: uv run python scripts/dev.py`}
            <div className="small">API: {API_URL}</div>
          </div>
        )}
        <ErrorBoundary resetKey={location.pathname}>
        <Routes>
          <Route path="/" element={<NewTest />} />
          <Route path="/history" element={<History />} />
          <Route path="/extension" element={<ExtensionPage />} />
          <Route path="/sessions/:id" element={<SessionPage />} />
          <Route path="/sessions/:id/bugs/:code" element={<BugPage />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
        </ErrorBoundary>
      </main>
    </>
  );
}
