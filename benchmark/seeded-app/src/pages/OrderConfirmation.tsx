import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { api, ApiError } from "../api";
import { money } from "../format";
import type { Order } from "../types";

export default function OrderConfirmation() {
  const { id } = useParams();
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ order: Order }>(`/api/orders/${id}`)
      .then((d) => setOrder(d.order))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load order"));
  }, [id]);

  if (error) return <p role="alert" className="error">{error}</p>;
  if (!order) return <p className="loading">Loading order…</p>;

  return (
    <section>
      <h1>Order confirmed</h1>
      <p>
        Order number: <strong>{order.id}</strong>
      </p>
      <ul className="summary card">
        {order.items.map((i) => (
          <li key={i.productId}>
            {i.quantity} × {i.name} <span>{money(i.price * i.quantity)}</span>
          </li>
        ))}
      </ul>
      <p className="summary-total">
        Total charged: <strong>{money(order.total)}</strong>
      </p>
      <p>Shipping to: {order.shipTo}</p>
    </section>
  );
}
