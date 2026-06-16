import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// All overrideable via env so `DEV_HOST=0.0.0.0 npm run dev:all` works.
// Per-service vars take precedence, falling back to DEV_HOST, then defaults.
const host = process.env.VITE_HOST || process.env.DEV_HOST || "127.0.0.1";
const vitePort = Number(process.env.VITE_PORT) || 5173;
const watcherHost = process.env.WATCHER_HOST || process.env.DEV_HOST || "127.0.0.1";
const watcherPort = Number(process.env.WATCHER_PORT) || 8233;
const apiTarget =
  process.env.SLACK_WATCHER_API || `http://${watcherHost}:${watcherPort}`;

// When DEV_HOST is 0.0.0.0 or any non-loopback address, requests via LAN
// hostnames (e.g. http://squirtle:5173) hit Vite's host allowlist. Default
// to allow-all in dev; override with VITE_ALLOWED_HOSTS=host1,host2 to lock
// it down.
const allowedHostsRaw = process.env.VITE_ALLOWED_HOSTS;
const allowedHosts: true | string[] =
  allowedHostsRaw == null || allowedHostsRaw === "all"
    ? true
    : allowedHostsRaw.split(",").map((s) => s.trim()).filter(Boolean);

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "Slack Watcher",
        short_name: "Watcher",
        description: "Schedule LLM prompts against polled Slack threads.",
        theme_color: "#0a0a0a",
        background_color: "#0a0a0a",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "/icon-512-maskable.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          { urlPattern: /^https?:\/\/[^/]+\/api\//, handler: "NetworkOnly" },
        ],
      },
    }),
  ],
  server: {
    host,
    port: vitePort,
    strictPort: true,
    allowedHosts,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
