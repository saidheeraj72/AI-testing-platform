import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { hasBug } from "../bugs";
import { EMAIL_RE } from "../format";
import type { Settings as SettingsData } from "../types";

export default function Settings() {
  const { setUser } = useAuth();
  const [form, setForm] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api<{ settings: SettingsData }>("/api/settings").then((d) => setForm(d.settings));
  }, []);

  if (!form) return <p className="loading">Loading settings…</p>;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setSaved(false);
    if (!form.displayName.trim()) return setError("Display name is required");
    // Seeded BUG-003: client-side email validation is skipped.
    if (!hasBug("BUG-003") && !EMAIL_RE.test(form.email.trim())) return setError("Enter a valid email address");

    setError(null);
    try {
      const d = await api<{ settings: SettingsData }>("/api/settings", { method: "PUT", body: form });
      setForm(d.settings);
      setUser({ name: d.settings.displayName, email: d.settings.email });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save settings");
    }
  }

  return (
    <section>
      <h1>Settings</h1>
      <form className="card form" onSubmit={save} noValidate>
        {error && <p role="alert" className="error">{error}</p>}
        {saved && <p role="status" className="notice">Settings saved.</p>}
        <label>
          Display name
          <input value={form.displayName} onChange={(e) => setForm({ ...form, displayName: e.target.value })} />
        </label>
        <label>
          Email
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={form.notifications} onChange={(e) => setForm({ ...form, notifications: e.target.checked })} />
          Email me product updates
        </label>
        <div className="actions">
          <button type="submit">Save settings</button>
        </div>
      </form>
    </section>
  );
}
