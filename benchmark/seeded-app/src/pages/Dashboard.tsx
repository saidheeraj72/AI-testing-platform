import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import type { CartLine, Customer } from "../types";

export default function Dashboard() {
  const { user } = useAuth();
  const [customerCount, setCustomerCount] = useState<number | null>(null);
  const [cartCount, setCartCount] = useState<number | null>(null);

  useEffect(() => {
    api<{ customers: Customer[] }>("/api/customers").then((d) => setCustomerCount(d.customers.length));
    api<{ items: CartLine[] }>("/api/cart").then((d) => setCartCount(d.items.reduce((n, i) => n + i.quantity, 0)));
  }, []);

  return (
    <section>
      <h1>Welcome back, {user?.name}</h1>
      <div className="stats">
        <Link to="/customers" className="card stat">
          <span className="stat-label">Customers</span>
          <span className="stat-value">{customerCount ?? "…"}</span>
        </Link>
        <Link to="/cart" className="card stat">
          <span className="stat-label">Items in cart</span>
          <span className="stat-value">{cartCount ?? "…"}</span>
        </Link>
      </div>
    </section>
  );
}
