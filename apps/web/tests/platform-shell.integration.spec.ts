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

test("@real-stack renders only the authenticated Branch customer directory", async ({
  page,
}) => {
  await page.goto("/customers");

  await expect(page.getByText("Manila / MNL")).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Real Stack Retail" }),
  ).toBeVisible();
  await expect(page.getByText("Cebu / CEB")).toHaveCount(0);
});

test("@real-stack renders movement-derived Warehouse availability", async ({
  page,
}) => {
  await page.goto("/inventory");

  await expect(
    page.getByRole("heading", { name: "Real Stack Cola 330 mL" }),
  ).toBeVisible();
  await expect(page.getByText("30.000000 EA")).toBeVisible();
  await expect(page.getByText("MNL-01 / REAL-AVAILABLE")).toBeVisible();
  await expect(page.getByText("REAL-LOT-A / 2027-12-31")).toBeVisible();
});

test("@real-stack creates a cash-on-delivery customer through the web docket", async ({
  page,
}) => {
  await page.goto("/customers");
  await page.getByRole("button", { name: "Open new-account docket" }).click();
  await page
    .getByLabel("Legal name", { exact: true })
    .fill("Web Journey Retail");
  await page.getByLabel("Account number", { exact: true }).fill("MNL-WEB-001");
  await page.getByLabel("Payment timing").selectOption("cash_on_delivery");
  await page.getByLabel("Payment terms").fill("Due upon delivery");
  await page.getByLabel("Contact name").fill("Lina Cruz");
  await page.getByLabel("Contact role").fill("Purchasing");
  await page.getByLabel("Email").fill("lina@web-journey.example");
  for (const kind of ["billing", "delivery"]) {
    await page
      .locator(`input[name="${kind}_line_1"]`)
      .fill("88 Browser Test Road");
    await page.locator(`input[name="${kind}_city"]`).fill("Manila");
    await page.locator(`input[name="${kind}_region"]`).fill("NCR");
    await page.locator(`input[name="${kind}_postal_code"]`).fill("1000");
    await page.locator(`input[name="${kind}_country"]`).fill("PH");
  }
  await page.getByRole("button", { name: "Create customer account" }).click();

  await expect(page.getByRole("status")).toContainText("MNL-WEB-001 created");
  await expect(
    page.getByRole("cell", { name: "Web Journey Retail" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Cash on delivery" }),
  ).toBeVisible();
});
