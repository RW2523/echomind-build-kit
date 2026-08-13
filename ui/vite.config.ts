import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Vite rejects requests whose Host header it does not recognise — a DNS-rebinding
    // guard. The tunnels this demo is shared over terminate on someone else's domain,
    // so those suffixes are named explicitly rather than disabling the check outright.
    allowedHosts: [".ts.net", ".trycloudflare.com"],
    // The API is same-origin in the browser during development, so SSE and uploads
    // need no CORS preflight and cookies/headers behave as they would in production.
    proxy: {
      "/chat": "http://localhost:8080",
      "/dataspaces": "http://localhost:8080",
      "/library": "http://localhost:8080",
      "/me": "http://localhost:8080",
      "/tools": "http://localhost:8080",
      "/actions": "http://localhost:8080",
      "/uploads": "http://localhost:8080",
      "/admin": "http://localhost:8080",
      "/threads": "http://localhost:8080",
      "/demo": "http://localhost:8080",
      "/conversations": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
      "/readyz": "http://localhost:8080",
    },
  },
});
