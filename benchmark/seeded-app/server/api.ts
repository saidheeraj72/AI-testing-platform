// In-memory backend for the seeded app, mounted as Vite dev-server middleware.
// State resets on restart or via POST /__bench/reset.
//
// /__bench/* endpoints exist for the benchmark harness only. The agent under
// test must never be told about them.

import type { IncomingMessage, ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import type { BugId } from "./bugs";

type Customer = { id: number; name: string; email: string; company: string; createdAt: string };
type Product = { id: number; name: string; price: number }; // price in cents
type CartLine = { productId: number; name: string; price: number; quantity: number };
type Order = { id: string; items: CartLine[]; total: number; shipTo: string; createdAt: string };
type Settings = { displayName: string; email: string; notifications: boolean };

const DEMO_USER = { email: "demo@example.com", password: "password123" };
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CUSTOMERS_LATENCY_MS = 600;

const PRODUCTS: Product[] = [
  { id: 1, name: "Wireless Mouse", price: 2499 },
  { id: 2, name: "Mechanical Keyboard", price: 8999 },
  { id: 3, name: "USB-C Hub", price: 3450 },
  { id: 4, name: "27-inch Monitor", price: 21900 },
];

function freshState() {
  return {
    sessions: new Set<string>(),
    customers: [
      { id: 1, name: "Ada Lovelace", email: "ada@analytical.example", company: "Analytical Engines", createdAt: "2026-01-12T09:00:00Z" },
      { id: 2, name: "Grace Hopper", email: "grace@cobol.example", company: "Compilers Inc", createdAt: "2026-02-03T14:30:00Z" },
      { id: 3, name: "Alan Turing", email: "alan@bletchley.example", company: "Bletchley Labs", createdAt: "2026-03-21T11:15:00Z" },
    ] as Customer[],
    nextCustomerId: 4,
    cart: [] as { productId: number; quantity: number }[],
    orders: new Map<string, Order>(),
    nextOrderNumber: 1001,
    settings: { displayName: "Demo User", email: DEMO_USER.email, notifications: true } as Settings,
  };
}

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

type Middleware = (req: IncomingMessage, res: ServerResponse, next: () => void) => void;

export function createApi(bugs: readonly BugId[]): Middleware {
  const has = (id: BugId) => bugs.includes(id);
  let state = freshState();

  function cartLines(): CartLine[] {
    return state.cart.map(({ productId, quantity }) => {
      const product = PRODUCTS.find((p) => p.id === productId)!;
      return { productId, name: product.name, price: product.price, quantity };
    });
  }

  function requireAuth(req: IncomingMessage) {
    const sid = readCookie(req, "sid");
    if (!sid || !state.sessions.has(sid)) throw new HttpError(401, "Not authenticated");
  }

  async function route(method: string, path: string, req: IncomingMessage, res: ServerResponse) {
    // --- benchmark harness ---
    if (method === "GET" && path === "/__bench/config") return send(res, 200, { bugs });
    if (method === "POST" && path === "/__bench/reset") {
      state = freshState();
      return send(res, 204);
    }

    // --- auth ---
    if (method === "POST" && path === "/api/login") {
      const body = await readJson(req);
      if (body.email !== DEMO_USER.email || body.password !== DEMO_USER.password) {
        throw new HttpError(401, "Invalid email or password");
      }
      const sid = randomUUID();
      state.sessions.add(sid);
      res.setHeader("Set-Cookie", `sid=${sid}; HttpOnly; Path=/; SameSite=Lax`);
      return send(res, 200, { user: currentUser() });
    }
    if (method === "POST" && path === "/api/logout") {
      const sid = readCookie(req, "sid");
      if (sid) state.sessions.delete(sid);
      res.setHeader("Set-Cookie", "sid=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0");
      return send(res, 204);
    }
    if (method === "GET" && path === "/api/me") {
      requireAuth(req);
      return send(res, 200, { user: currentUser() });
    }

    requireAuth(req);

    // --- customers ---
    if (method === "GET" && path === "/api/customers") {
      await sleep(CUSTOMERS_LATENCY_MS);
      return send(res, 200, { customers: [...state.customers].reverse() });
    }
    if (method === "POST" && path === "/api/customers") {
      const body = await readJson(req);
      const name = String(body.name ?? "").trim();
      const email = String(body.email ?? "").trim();
      const company = String(body.company ?? "").trim();
      if (!name) throw new HttpError(400, "Name is required");
      if (!EMAIL_RE.test(email)) throw new HttpError(400, "Enter a valid email address");
      if (has("BUG-001")) {
        // Seeded BUG-001: customer creation always fails server-side.
        console.error("[seeded-app] BUG-001: TypeError: Cannot read properties of undefined (reading 'accountId')");
        throw new HttpError(500, "Internal Server Error");
      }
      const customer: Customer = { id: state.nextCustomerId++, name, email, company, createdAt: new Date().toISOString() };
      state.customers.push(customer);
      return send(res, 201, { customer });
    }

    // --- shop / cart / orders ---
    if (method === "GET" && path === "/api/products") return send(res, 200, { products: PRODUCTS });

    if (method === "GET" && path === "/api/cart") return send(res, 200, { items: cartLines() });
    if (method === "POST" && path === "/api/cart/items") {
      const body = await readJson(req);
      const productId = Number(body.productId);
      const quantity = Number(body.quantity ?? 1);
      if (!PRODUCTS.some((p) => p.id === productId)) throw new HttpError(404, "Product not found");
      if (!Number.isInteger(quantity) || quantity < 1) throw new HttpError(400, "Quantity must be a positive whole number");
      const line = state.cart.find((l) => l.productId === productId);
      if (line) line.quantity += quantity;
      else state.cart.push({ productId, quantity });
      return send(res, 200, { items: cartLines() });
    }
    const cartItem = path.match(/^\/api\/cart\/items\/(\d+)$/);
    if (cartItem && (method === "PUT" || method === "DELETE")) {
      const productId = Number(cartItem[1]);
      const quantity = method === "DELETE" ? 0 : Number((await readJson(req)).quantity);
      if (!Number.isInteger(quantity) || quantity < 0) throw new HttpError(400, "Quantity must be a whole number");
      state.cart = quantity === 0
        ? state.cart.filter((l) => l.productId !== productId)
        : state.cart.map((l) => (l.productId === productId ? { ...l, quantity } : l));
      return send(res, 200, { items: cartLines() });
    }

    if (method === "POST" && path === "/api/orders") {
      const body = await readJson(req);
      const shipTo = String(body.shipTo ?? "").trim();
      const items = cartLines();
      if (items.length === 0) throw new HttpError(400, "Your cart is empty");
      if (!shipTo) throw new HttpError(400, "Shipping address is required");
      const order: Order = {
        id: `ORD-${state.nextOrderNumber++}`,
        items,
        total: items.reduce((sum, l) => sum + l.price * l.quantity, 0),
        shipTo,
        createdAt: new Date().toISOString(),
      };
      state.orders.set(order.id, order);
      state.cart = [];
      return send(res, 201, { order });
    }
    const orderPath = path.match(/^\/api\/orders\/([\w-]+)$/);
    if (method === "GET" && orderPath) {
      const order = state.orders.get(orderPath[1]);
      if (!order) throw new HttpError(404, "Order not found");
      return send(res, 200, { order });
    }

    // --- settings ---
    if (method === "GET" && path === "/api/settings") return send(res, 200, { settings: state.settings });
    if (method === "PUT" && path === "/api/settings") {
      const body = await readJson(req);
      const displayName = String(body.displayName ?? "").trim();
      const email = String(body.email ?? "").trim();
      if (!displayName) throw new HttpError(400, "Display name is required");
      // Seeded BUG-003: the server also skips email validation.
      if (!has("BUG-003") && !EMAIL_RE.test(email)) throw new HttpError(400, "Enter a valid email address");
      state.settings = { displayName, email, notifications: Boolean(body.notifications) };
      return send(res, 200, { settings: state.settings });
    }

    throw new HttpError(404, "Not found");
  }

  function currentUser() {
    return { name: state.settings.displayName, email: state.settings.email };
  }

  return (req, res, next) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (!url.pathname.startsWith("/api/") && !url.pathname.startsWith("/__bench/")) return next();

    route(req.method ?? "GET", url.pathname, req, res).catch((err: unknown) => {
      if (err instanceof HttpError) return send(res, err.status, { error: err.message });
      console.error(err);
      send(res, 500, { error: "Internal Server Error" });
    });
  };
}

function send(res: ServerResponse, status: number, body?: unknown) {
  res.statusCode = status;
  if (body === undefined) return res.end();
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  const raw = Buffer.concat(chunks).toString("utf8");
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
  } catch {
    // fall through
  }
  throw new HttpError(400, "Request body must be a JSON object");
}

function readCookie(req: IncomingMessage, name: string): string | undefined {
  for (const part of (req.headers.cookie ?? "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return undefined;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
