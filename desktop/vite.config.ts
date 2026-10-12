import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server port is fixed: tauri.conf.json's devUrl points at it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // vitest transpiles TSX with esbuild, whose default JSX mode is the
  // classic runtime (React.createElement) — the app build uses the
  // automatic runtime via @vitejs/plugin-react, so tests that render
  // REAL components (not mocks) crash with "React is not defined".
  // Align the two: automatic runtime everywhere.
  esbuild: { jsx: "automatic" },
  resolve: {
    // The canvas package is a file: symlink into pv-beehive-web; without
    // this vite resolves its react imports from the WEB repo's node_modules
    // → two React copies at runtime ("Invalid hook call").
    preserveSymlinks: true,
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    // No /api proxy anymore: the unified posture (#36) sends every beehive
    // call through the local runtime, which signs with the keychain AK/SK.
    // The vite proxy previously masked the production-bundle breakage where
    // webview-relative /api fetches hit the webview's own origin.
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: { target: "esnext" },
});
