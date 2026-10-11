import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately (uvicorn on :8000); in production FastAPI serves dist/.
// three.js and Fluent are split into their own chunks: they change rarely, so browsers keep them cached.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: "http://localhost:8000", ws: true } } },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three"],
          fluent: ["@fluentui/react-components"],
        },
      },
    },
  },
});
