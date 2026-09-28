import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server port is fixed: tauri.conf.json's devUrl points at it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    proxy: {
      // A3 S1 canvas: the skilyst-studio package talks to the beehive dev API.
      // The API's CORS allowlist (bee.verse4.pet + localhost:3000) does not
      // include :1420, so dev traffic goes through this same-origin proxy
      // (REST + jobs WS). Production wiring (a core CORS entry for the desktop
      // origin) is tracked on pv-skilyst-agent#10.
      "/api": {
        target: "https://beehive-api.verse4.pet",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: { target: "esnext" },
});
