import { expect, test, type Page } from "@playwright/test";

export const overview = {
  action_queue: [
    {
      age_minutes: 65,
      amount: "1971.20",
      branch_code: "MNL",
      currency: "PHP",
      href: "/sales-orders/approvals",
      kind: "approval",
      next_action: "Review order",
      owner: "demo-maker",
      record_id: "ac0bddfb-d383-5daf-ac6b-aaf078d64957",
      reference: "SO-DEMO-001",
      status: "awaiting_approval",
      title: "Harbor & Pine Retail",
      urgency: "medium",
    },
  ],
  branches: [
    {
      branch_id: "6eedded7-4bf3-4b1d-b221-f4304c4e7195",
      code: "MNL",
      name: "Manila",
    },
  ],
  finance: {
    collected_value: "600",
    currency: "PHP",
    outstanding_receivables: "1371.20",
    overdue_balances: "0",
    posted_invoices: 1,
    posted_value: "1971.20",
    receipts_awaiting_value: "500",
    receipts_awaiting_verification: 1,
  },
  from_date: "2026-07-25",
  generated_at: "2026-08-24T08:00:00Z",
  inventory: {
    available: "152",
    blocked_lots: 0,
    low_stock_items: 1,
    pending_adjustments: 1,
    pending_transfers: 1,
    reserved: "21",
    unit: "base units",
  },
  metrics: [
    { key: "awaiting_approval", label: "Orders awaiting approval", count: 1 },
    { key: "ready_to_pick", label: "Orders ready to pick", count: 1 },
    {
      key: "awaiting_confirmation",
      label: "Deliveries awaiting confirmation",
      count: 1,
    },
    {
      key: "awaiting_verification",
      label: "Payments awaiting verification",
      count: 1,
    },
    { key: "low_stock", label: "Low-stock items", count: 1 },
    {
      key: "receivables",
      label: "Outstanding receivables",
      amount: "1371.20",
      currency: "PHP",
    },
  ],
  pipeline: [
    "Approval",
    "Reservation",
    "Picking",
    "Delivery",
    "Invoicing",
    "Payment",
  ].map((label) => ({
    count: 1,
    currency: "PHP",
    key: label.toLowerCase(),
    label,
    value: "1971.20",
  })),
  recent_activity: [],
  selected_branch_id: null,
  to_date: "2026-08-24",
};

export async function mockOverview(page: Page, value = overview) {
  await page.route("**/api/operations/overview?**", (route) =>
    route.fulfill({
      body: JSON.stringify(value),
      contentType: "application/json",
      status: 200,
    }),
  );
}

test("operations overview renders authoritative work and supports search", async ({
  page,
}) => {
  await mockOverview(page);
  await page.goto("/operations");
  await expect(page.getByText("Harbor & Pine Retail")).toBeVisible();
  await expect(page.getByText("₱1,371").first()).toBeVisible();
  await page
    .getByPlaceholder("Customer, reference, owner, or status")
    .fill("missing");
  await expect(
    page.getByRole("heading", { name: "No work matches this search" }),
  ).toBeVisible();
});

test("operations overview keeps current values visible while refreshing", async ({
  page,
}) => {
  let calls = 0;
  await page.route("**/api/operations/overview?**", async (route) => {
    calls += 1;
    if (calls > 1) await new Promise((resolve) => setTimeout(resolve, 300));
    await route.fulfill({
      body: JSON.stringify(overview),
      contentType: "application/json",
      status: 200,
    });
  });
  await page.goto("/operations");
  await page.getByRole("button", { name: "Refresh operational data" }).click();
  await expect(page.getByText("Refreshing live data")).toBeVisible();
  await expect(page.getByText("Harbor & Pine Retail")).toBeVisible();
});

test("operations overview shows honest empty and error states", async ({
  page,
}) => {
  await mockOverview(page, {
    ...overview,
    action_queue: [],
    recent_activity: [],
  });
  await page.goto("/operations");
  await expect(
    page.getByRole("heading", { name: "No work requires attention" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "No activity in this range" }),
  ).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await page.route("**/api/operations/overview?**", (route) =>
    route.fulfill({
      body: JSON.stringify({ error: { correlation_id: "demo-correlation" } }),
      contentType: "application/json",
      status: 503,
    }),
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Operational data could not be loaded" }),
  ).toBeVisible();
  await expect(page.getByText("demo-correlation")).toBeVisible();
});

test("operations overview has no horizontal overflow on mobile", async ({
  page,
}) => {
  await mockOverview(page);
  await page.goto("/operations");
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
