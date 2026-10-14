import { defineConfig, devices } from "@playwright/test";

// The e2e suite runs against the real showcase server (python -m aris.demo.server)
// serving this app's production build: real BankBot decisions over HTTP, real
// measured data. Nothing is mocked except where a test says so (offline mode).
const PORT = Number(process.env.ARIS_E2E_PORT ?? 8790);
const python = process.env.ARIS_PYTHON ?? "python";

export default defineConfig({
  testDir: "e2e",
  timeout: 45_000,
  fullyParallel: false, // one shared backend: tests reset it, so run serially
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && ${python} -m aris.demo.server --port ${PORT} --dist dist`,
    url: `http://127.0.0.1:${PORT}/demo/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { ARIS_SHOWCASE_INVESTIGATOR_KEY: "e2e-key", ARIS_DEV_MODE: "1" },
  },
});
