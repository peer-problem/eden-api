import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.EDEN_BROWSER_TEST_PORT ?? 4173);
const baseURL = `http://127.0.0.1:${port}/dashboard/`;

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: true,
  reporter: "line",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npm run dev -- --port ${port}`,
    url: baseURL,
    reuseExistingServer: true,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
});
