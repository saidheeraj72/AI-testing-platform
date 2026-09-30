import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { EMAIL_RE } from "../format";

export default function CustomerNew() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setError("Name is required");
    if (!EMAIL_RE.test(email.trim())) return setError("Enter a valid email address");

    setError(null);
    setSubmitting(true);
    try {
      await api("/api/customers", { method: "POST", body: { name, email, company } });
      navigate("/customers", { state: { created: name.trim() } });
    } catch (err) {
      setError(err instanceof ApiError && err.status < 500 ? err.message : "Could not create customer. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section>
      <h1>New customer</h1>
      <form className="card form" onSubmit={submit} noValidate>
        {error && <p role="alert" className="error">{error}</p>}
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Company (optional)
          <input value={company} onChange={(e) => setCompany(e.target.value)} />
        </label>
        <div className="actions">
          <button type="submit" disabled={submitting}>
            {submitting ? "Saving…" : "Create customer"}
          </button>
          <Link to="/customers">Cancel</Link>
        </div>
      </form>
    </section>
  );
}
