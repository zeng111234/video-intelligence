import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 1001,
    host: "0.0.0.0",
    strictPort: true,
  },
  preview: {
    port: 1001,
    host: "0.0.0.0",
    strictPort: true,
  },
});
