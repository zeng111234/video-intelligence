import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(() => {
  return {
    plugins: [react()],
    server: {
      port: 1001,
      host: "127.0.0.1",
      strictPort: true,
      proxy: {
        "/api": {
          target: "http://localhost:2001",
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: 1001,
      host: "127.0.0.1",
      strictPort: true,
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
