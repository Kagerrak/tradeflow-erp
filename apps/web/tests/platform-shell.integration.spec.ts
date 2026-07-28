import { expect, test } from "@playwright/test";

test.skip(
  process.env.TRADEFLOW_REAL_STACK !== "1",
  "Runs only against the migrated real-stack acceptance environment.",
);

test("@real-stack renders the authenticated API and PostgreSQL session", async ({
  page,
}) => {
  const sessionResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/platform-session"),
  );

  await page.goto("/");
  const response = await sessionResponse;

  await expect(
    page.getByRole("heading", { name: "Platform handoff is ready" }),
  ).toBeVisible();
  await expect(page.getByText("Local Platform Operator")).toBeVisible();
  await expect(page.getByText("ready", { exact: true })).toBeVisible();
  expect(response.status()).toBe(200);
  expect(response.headers()["x-correlation-id"]).toMatch(/^[0-9a-f-]{36}$/u);
});
