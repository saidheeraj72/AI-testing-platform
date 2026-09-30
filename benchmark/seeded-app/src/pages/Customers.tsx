import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { api } from "../api";
import type { Customer } from "../types";

export default function Customers() {
  const location = useLocation();
  const created = (location.state as { created?: string } | null)?.created;
  const [customers, setCustomers] = useState<Customer[] | null>(null);

  useEffect(() => {
    api<{ customers: Customer[] }>("/api/customers").then((d) => setCustomers(d.customers));
  }, []);

  return (
    <section>
      <div className="page-header">
        <h1>Customers</h1>
        <Link to="/customers/new" className="button">
          New customer
        </Link>
      </div>
      {created && <p role="status" className="notice">Customer {created} was created.</p>}
      {customers === null ? (
        <p className="loading">Loading customers…</p>
      ) : (
        <table aria-label="Customers">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Company</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{c.email}</td>
                <td>{c.company || "—"}</td>
                <td>{new Date(c.createdAt).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
