/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const designSystem = fileURLToPath(new URL("../aris-design-system", import.meta.url));
// The showcase server (python -m aris.demo.server) holds the live BankBot API.
const backend = process.env.ARIS_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@aris": designSystem },
    // aris.esm.js lives outside this app; resolve its `react` import from
    // here so the page never ends up with two copies of React.
    dedupe: ["react", "react-dom"],
  },
  server: {
    fs: { allow: [".", designSystem] },
    proxy: { "/api": backend, "/demo": backend },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
  },
});
