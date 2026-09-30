import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createApi } from "./server/api";
import { parseSeededBugs } from "./server/bugs";

const bugs = parseSeededBugs(process.env.SEEDED_BUGS);

export default defineConfig({
  plugins: [
    react(),
    {
      name: "seeded-api",
      configureServer(server) {
        server.middlewares.use(createApi(bugs));
        server.config.logger.info(`\n  Seeded bugs active: ${bugs.length ? bugs.join(", ") : "none"}\n`);
      },
    },
  ],
  // Client-side bugs (BUG-002, BUG-003, BUG-004) read the same flags.
  define: { __SEEDED_BUGS__: JSON.stringify(bugs) },
  server: { port: 3000, strictPort: true },
});
