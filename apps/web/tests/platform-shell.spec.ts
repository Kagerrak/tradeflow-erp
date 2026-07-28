import { expect, test } from "@playwright/test";

const correlationId = "2c95f0a1-f768-4194-bd19-1695696207df";

test("shows progress while checking the operator session", async ({ page }) => {
  let releaseResponse: (() => void) | undefined;
  await page.route("**/api/platform-session", async (route) => {
    await new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    await route.fulfill({
      body: JSON.stringify({
        correlationId,
        kind: "unauthenticated",
      }),
      contentType: "application/json",
      status: 401,
    });
  });

  await page.goto("/");

  await expect(
    page.getByRole("status", {
      name: "Checking identity, API, and database",
    }),
  ).toBeVisible();
  releaseResponse?.();
});

test("shows the authoritative ready session", async ({ page }) => {
  await page.route("**/api/platform-session", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        correlationId,
        database: "ready",
        kind: "ready",
        service: "tradeflow-api",
        user: {
          capabilities: ["platform:read"],
          displayName: "Platform Tester",
          subject: "user-123",
        },
      }),
      contentType: "application/json",
      status: 200,
    });
  });

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Platform handoff is ready" }),
  ).toBeVisible();
  await expect(page.getByText("Platform Tester")).toBeVisible();
  await expect(page.getByText(correlationId)).toBeVisible();
});

test("gives an unauthenticated operator a specific next action", async ({
  page,
}) => {
  await page.route("**/api/platform-session", async (route) => {
    await route.fulfill({
      body: JSON.stringify({ correlationId, kind: "unauthenticated" }),
      contentType: "application/json",
      status: 401,
    });
  });

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Sign in to continue" }),
  ).toBeVisible();
  await expect(page.getByText("Open your identity provider")).toBeVisible();
});

test("explains a capability denial without presenting a retry", async ({
  page,
}) => {
  await page.route("**/api/platform-session", async (route) => {
    await route.fulfill({
      body: JSON.stringify({ correlationId, kind: "forbidden" }),
      contentType: "application/json",
      status: 403,
    });
  });

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Platform access is not assigned" }),
  ).toBeVisible();
  await expect(page.getByText("Ask an operations administrator")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Retry connection" }),
  ).toHaveCount(0);
});

test("lets the operator retry a temporarily unavailable platform", async ({
  page,
}) => {
  let requestCount = 0;
  await page.route("**/api/platform-session", async (route) => {
    requestCount += 1;
    await route.fulfill({
      body: JSON.stringify(
        requestCount === 1
          ? { correlationId, kind: "unavailable" }
          : {
              correlationId,
              database: "ready",
              kind: "ready",
              service: "tradeflow-api",
              user: {
                capabilities: ["platform:read"],
                displayName: "Platform Tester",
                subject: "user-123",
              },
            },
      ),
      contentType: "application/json",
      status: requestCount === 1 ? 503 : 200,
    });
  });

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "TradeFlow is temporarily unavailable" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Retry connection" }).click();

  await expect(
    page.getByRole("heading", { name: "Platform handoff is ready" }),
  ).toBeVisible();
});
