import { defineConfig, devices } from "@playwright/test";

/* E2E against a local DEMO server (embedded Postgres + deterministic seed). Each run uses a fresh database directory. */
const port = Number(process.env.E2E_PORT ?? 3210);
const dbDir = process.env.E2E_PGLITE_DIR ?? `.apex-local/e2e-${Date.now()}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://localhost:${port}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "iphone", use: { ...devices["iPhone 13"], viewport: { width: 390, height: 844 }, browserName: "chromium" } },
  ],
  webServer: {
    command: `node scripts/gen-embedded.mjs && next ${process.env.E2E_PROD ? "start" : "dev"} --port ${port}`,
    url: `http://localhost:${port}/login`,
    timeout: 180_000,
    reuseExistingServer: false,
    env: { APEX_MODE: "DEMO", APEX_ALLOW_LOCAL_DEV_LOGIN: "1", APEX_PGLITE_DIR: dbDir, DATABASE_URL: "" },
  },
});
