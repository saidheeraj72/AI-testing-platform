import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_API_URL and VITE_API_TOKEN are provided by scripts/dev.py (or the packaged app).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, host: "127.0.0.1" },
});
