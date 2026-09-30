import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { cartTotal, money } from "../format";
import type { CartLine, Order } from "../types";

export default function Checkout() {
  const navigate = useNavigate();
  const [items, setItems] = useState<CartLine[] | null>(null);
  const [shipTo, setShipTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api<{ items: CartLine[] }>("/api/cart").then((d) => setItems(d.items));
  }, []);

  async function placeOrder(event: FormEvent) {
    event.preventDefault();
    if (!shipTo.trim()) return setError("Shipping address is required");
    setError(null);
    setSubmitting(true);
    try {
      const d = await api<{ order: Order }>("/api/orders", { method: "POST", body: { shipTo } });
      navigate(`/orders/${d.order.id}`);
    } catch (err) {
      setError(err instanceof ApiError && err.status < 500 ? err.message : "Could not place order. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (items === null) return <p className="loading">Loading checkout…</p>;
  if (items.length === 0) {
    return (
      <section>
        <h1>Checkout</h1>
        <p>
          Your cart is empty. <Link to="/shop">Browse the shop</Link>
        </p>
      </section>
    );
  }

  return (
    <section>
      <h1>Checkout</h1>
      <div className="card">
        <h2>Order summary</h2>
        <ul className="summary">
          {items.map((i) => (
            <li key={i.productId}>
              {i.quantity} × {i.name} <span>{money(i.price * i.quantity)}</span>
            </li>
          ))}
        </ul>
        <p className="summary-total">
          Order total: <strong>{money(cartTotal(items))}</strong>
        </p>
      </div>
      <form className="card form" onSubmit={placeOrder} noValidate>
        {error && <p role="alert" className="error">{error}</p>}
        <label>
          Shipping address
          <textarea value={shipTo} onChange={(e) => setShipTo(e.target.value)} rows={3} />
        </label>
        <div className="actions">
          <button type="submit" disabled={submitting}>
            {submitting ? "Placing order…" : "Place order"}
          </button>
          <Link to="/cart">Back to cart</Link>
        </div>
      </form>
    </section>
  );
}
