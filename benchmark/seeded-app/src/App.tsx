import { Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router";
import { api } from "./api";
import { useAuth } from "./auth";
import { hasBug } from "./bugs";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Customers from "./pages/Customers";
import CustomerNew from "./pages/CustomerNew";
import Shop from "./pages/Shop";
import Cart from "./pages/Cart";
import Checkout from "./pages/Checkout";
import OrderConfirmation from "./pages/OrderConfirmation";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/customers" element={<Customers />} />
          <Route path="/customers/new" element={<CustomerNew />} />
          <Route path="/shop" element={<Shop />} />
          <Route path="/cart" element={<Cart />} />
          <Route path="/checkout" element={<Checkout />} />
          <Route path="/orders/:id" element={<OrderConfirmation />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}

function RequireAuth() {
  const { user, loading, loggedOut } = useAuth();
  const location = useLocation();
  if (loading) return <p className="loading">Loading…</p>;
  if (!user) return <Navigate to="/login" replace state={loggedOut ? { loggedOut } : { from: location.pathname }} />;
  return <Outlet />;
}

function Layout() {
  const { user, signOut } = useAuth();

  async function logout() {
    await api("/api/logout", { method: "POST" });
    // Seeded BUG-004: the session ends server-side but the UI never redirects.
    if (hasBug("BUG-004")) return;
    signOut(); // RequireAuth redirects to /login
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Acme CRM</span>
        <nav aria-label="Main">
          <NavLink to="/dashboard">Dashboard</NavLink>
          <NavLink to="/customers">Customers</NavLink>
          <NavLink to="/shop">Shop</NavLink>
          <NavLink to="/cart">Cart</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="user">
          <span>{user?.name}</span>
          <button type="button" className="secondary" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}

function NotFound() {
  return (
    <section>
      <h1>Page not found</h1>
      <p>The page you are looking for does not exist.</p>
    </section>
  );
}
