import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: {
    port: 4173,
    strictPort: true,
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000",
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    target: "es2022",
  },
});
