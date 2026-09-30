import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { money } from "../format";
import type { Product } from "../types";

export default function Shop() {
  const [products, setProducts] = useState<Product[] | null>(null);
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<{ products: Product[] }>("/api/products").then((d) => setProducts(d.products));
  }, []);

  async function add(product: Product) {
    const quantity = Number(quantities[product.id] ?? "1");
    await api("/api/cart/items", { method: "POST", body: { productId: product.id, quantity } });
    setMessage(`Added ${quantity} × ${product.name} to your cart.`);
  }

  return (
    <section>
      <div className="page-header">
        <h1>Shop</h1>
        <Link to="/cart" className="button secondary">
          View cart
        </Link>
      </div>
      {message && <p role="status" className="notice">{message}</p>}
      {products === null ? (
        <p className="loading">Loading products…</p>
      ) : (
        <ul className="products">
          {products.map((p) => (
            <li key={p.id} className="card product">
              <h2>{p.name}</h2>
              <p className="price">{money(p.price)}</p>
              <label>
                Quantity
                <input
                  type="number"
                  min={1}
                  value={quantities[p.id] ?? "1"}
                  onChange={(e) => setQuantities({ ...quantities, [p.id]: e.target.value })}
                  aria-label={`Quantity for ${p.name}`}
                />
              </label>
              <button type="button" onClick={() => add(p)} aria-label={`Add ${p.name} to cart`}>
                Add to cart
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
