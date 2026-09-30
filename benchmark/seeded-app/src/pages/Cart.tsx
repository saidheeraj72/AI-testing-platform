import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { cartTotal, money } from "../format";
import type { CartLine } from "../types";

export default function Cart() {
  const [items, setItems] = useState<CartLine[] | null>(null);

  useEffect(() => {
    api<{ items: CartLine[] }>("/api/cart").then((d) => setItems(d.items));
  }, []);

  async function setQuantity(productId: number, quantity: number) {
    if (!Number.isInteger(quantity) || quantity < 0) return;
    const d = await api<{ items: CartLine[] }>(`/api/cart/items/${productId}`, { method: "PUT", body: { quantity } });
    setItems(d.items);
  }

  async function remove(productId: number) {
    const d = await api<{ items: CartLine[] }>(`/api/cart/items/${productId}`, { method: "DELETE" });
    setItems(d.items);
  }

  if (items === null) return <p className="loading">Loading cart…</p>;

  return (
    <section>
      <h1>Your cart</h1>
      {items.length === 0 ? (
        <p>
          Your cart is empty. <Link to="/shop">Browse the shop</Link>
        </p>
      ) : (
        <>
          <table aria-label="Cart items">
            <thead>
              <tr>
                <th>Product</th>
                <th>Unit price</th>
                <th>Quantity</th>
                <th>Line total</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.productId}>
                  <td>{item.name}</td>
                  <td>{money(item.price)}</td>
                  <td>
                    <input
                      type="number"
                      min={0}
                      defaultValue={item.quantity}
                      onBlur={(e) => setQuantity(item.productId, Number(e.target.value))}
                      aria-label={`Quantity for ${item.name}`}
                    />
                  </td>
                  <td>{money(item.price * item.quantity)}</td>
                  <td>
                    <button type="button" className="link" onClick={() => remove(item.productId)} aria-label={`Remove ${item.name}`}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th colSpan={3} scope="row">
                  Total
                </th>
                <td data-testid="cart-total">{money(cartTotal(items))}</td>
                <td />
              </tr>
            </tfoot>
          </table>
          <div className="actions">
            <Link to="/checkout" className="button">
              Proceed to checkout
            </Link>
            <Link to="/shop">Continue shopping</Link>
          </div>
        </>
      )}
    </section>
  );
}
