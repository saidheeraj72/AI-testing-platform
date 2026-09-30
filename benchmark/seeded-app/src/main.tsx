import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { AuthProvider } from "./auth";
import App from "./App";
import "./styles.css";

// NOISE-002: pre-existing console error on every page load. Must be filtered by the baseline.
console.error("[legacy-widget] Failed to initialise: feature flag service unavailable");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
