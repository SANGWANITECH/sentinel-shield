import { defineConfig } from "vite";

// No React plugin needed: Vite's built-in esbuild compiles JSX ("automatic" runtime).
// /api is proxied to FastAPI so the browser only talks to one origin.
const api = { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } };

export default defineConfig({
  esbuild: { jsx: "automatic" },
  server: { port: 5173, strictPort: true, proxy: api },
  preview: { port: 4173, strictPort: true, proxy: api },
});
