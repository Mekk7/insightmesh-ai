import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000", // FastAPI backend
        changeOrigin: true,
        secure: false,
      },
    },
  },
  test: {
    // Vitest config — kept inline in vite.config.js per Vitest docs
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.js"],
    css: false, // don't process Tailwind during tests
    coverage: {
      reporter: ["text", "html"],
      exclude: ["node_modules/", "src/test/", "dist/"],
    },
  },
});
