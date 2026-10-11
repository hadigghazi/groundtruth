import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately (uvicorn on :8000); in production FastAPI serves dist/.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: "http://localhost:8000", ws: true } } },
  build: { outDir: "dist", chunkSizeWarningLimit: 900 },
});
