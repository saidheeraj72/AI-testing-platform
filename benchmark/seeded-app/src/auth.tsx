import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { User } from "./types";

type AuthState = {
  user: User | null;
  loading: boolean;
  /** True after an explicit logout, until the next sign-in. */
  loggedOut: boolean;
  setUser: (user: User) => void;
  signOut: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [loggedOut, setLoggedOut] = useState(false);

  useEffect(() => {
    // NOISE-003: this returns 401 when logged out. Expected, not a bug.
    api<{ user: User }>("/api/me")
      .then((data) => setUser(data.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const value: AuthState = {
    user,
    loading,
    loggedOut,
    setUser: (next) => {
      setUser(next);
      setLoggedOut(false);
    },
    signOut: () => {
      setUser(null);
      setLoggedOut(true);
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
