import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig(({ mode }) => {
  // These settings stay in the Vite server. They are never VITE_* browser vars.
  const envDir = resolve(import.meta.dirname, "..");
  const local = loadEnv(mode, envDir, "EDEN_");
  const upstream = local.EDEN_EXPLORER_UPSTREAM;
  const token = local.EDEN_EXPLORER_TOKEN;
  const explorerGuard: Plugin = {
    name: "eden-explorer-connection",
    configureServer(server) {
      server.middlewares.use("/internal/explorer", (req, res, next) => {
        if (upstream && token) return next();
        res.statusCode = 503;
        res.setHeader("Content-Type", "application/json");
        res.end(
          JSON.stringify({
            error: {
              code: "EXPLORER_NOT_CONFIGURED",
              message:
                "DB 연결 전입니다. 테이블 구조와 관계는 로컬 스키마로 탐색할 수 있습니다.",
            },
          }),
        );
      });
    },
  };
  return {
    envDir,
    plugins: [react(), explorerGuard],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "/eden-api": {
          target: "https://api.edenapi.org",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/eden-api/, ""),
          proxyTimeout: 35000,
        },
        ...(upstream && token
          ? {
              "/internal/explorer": {
                target: upstream,
                changeOrigin: true,
                headers: { Authorization: `Bearer ${token}` },
                proxyTimeout: 15000,
              },
            }
          : {}),
      },
    },
  };
});
