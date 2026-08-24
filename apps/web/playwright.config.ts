import { defineConfig, devices } from "@playwright/test";

const externalBaseUrl = process.env.PLAYWRIGHT_BASE_URL;

export default defineConfig({
  expect: {
    timeout: 5_000,
  },
  fullyParallel: true,
  retries: process.env.CI === undefined ? 0 : 2,
  testDir: "./tests",
  use: {
    baseURL: externalBaseUrl ?? "http://127.0.0.1:3100",
    trace: "on-first-retry",
  },
  ...(externalBaseUrl
    ? {}
    : {
        webServer: {
          command: "pnpm dev --port 3100",
          reuseExistingServer: process.env.CI === undefined,
          timeout: 120_000,
          url: "http://127.0.0.1:3100",
        },
      }),
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-web",
      use: {
        ...devices["iPhone 13"],
        browserName: "chromium",
      },
    },
  ],
});
