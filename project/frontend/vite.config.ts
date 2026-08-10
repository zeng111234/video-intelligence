import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VIDEOINSIGHT_");
  const apiProxyTarget = env.VIDEOINSIGHT_API_PROXY_TARGET || "http://localhost:2001";
  return {
    plugins: [react()],
    server: {
      port: 1001,
      host: "127.0.0.1",
      strictPort: true,
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: 1001,
      host: "127.0.0.1",
      strictPort: true,
    },
    test: {
      setupFiles: ["./src/test/setup.ts"],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-react": ["react", "react-dom", "react-router-dom"],
            "vendor-antd": ["antd", "@ant-design/icons"],
            "vendor-recharts": ["recharts"],
          },
        },
      },
    },
  };
});
