import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API target for the /api proxy: the Makefile exports VITE_API_TARGET=
// http://127.0.0.1:$(APP_API_PORT) when running vite (see `make app` /
// `make app-web`), so the proxy always follows APP_API_PORT. Falls back to
// the default port for direct `vite`/`bun run dev` invocations.
const apiTarget = process.env.VITE_API_TARGET || "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
