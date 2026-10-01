import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import App from "./App";
import { initDesktopConfig } from "./api/client";
import "./styles.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 1000, refetchOnWindowFocus: false } } });

const root = createRoot(document.getElementById("root")!);
root.render(<p className="empty" style={{ padding: "2rem" }}>Starting AI Tester…</p>);

// In the desktop app the engine starts alongside the window; wait for it before rendering the app.
initDesktopConfig((seconds) => {
  if (seconds > 5) root.render(<p className="empty" style={{ padding: "2rem" }}>Starting the AI Tester engine… ({Math.round(seconds)}s)</p>);
}).finally(() =>
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  ),
);
