import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server port is fixed: tauri.conf.json's devUrl points at it.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  envPrefix: ["VITE_", "TAURI_"],
  build: { target: "esnext" },
});
