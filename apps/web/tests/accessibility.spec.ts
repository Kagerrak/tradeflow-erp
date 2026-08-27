import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const wcagTags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

test("commercial homepage passes automated WCAG A and AA checks", async ({
  page,
}) => {
  await page.goto("/");
  const results = await new AxeBuilder({ page }).withTags(wcagTags).analyze();
  expect(results.violations).toEqual([]);
});

test("operations overview passes automated WCAG A and AA checks", async ({
  page,
}) => {
  await page.route("**/api/operations/overview?**", (route) =>
    route.fulfill({
      body: JSON.stringify({
        action_queue: [],
        branches: [],
        finance: {
          collected_value: "0",
          currency: "PHP",
          outstanding_receivables: "0",
          overdue_balances: "0",
          posted_invoices: 0,
          posted_value: "0",
          receipts_awaiting_value: "0",
          receipts_awaiting_verification: 0,
        },
        from_date: "2026-07-25",
        generated_at: "2026-08-24T08:00:00Z",
        inventory: {
          available: "0",
          blocked_lots: 0,
          low_stock_items: 0,
          pending_adjustments: 0,
          pending_transfers: 0,
          reserved: "0",
          unit: "base units",
        },
        metrics: [],
        pipeline: [],
        recent_activity: [],
        selected_branch_id: null,
        to_date: "2026-08-24",
      }),
      contentType: "application/json",
      status: 200,
    }),
  );
  await page.goto("/operations");
  const results = await new AxeBuilder({ page }).withTags(wcagTags).analyze();
  expect(results.violations).toEqual([]);
});

test("primary commercial journey is keyboard reachable", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to main content" }),
  ).toBeFocused();
  await page.getByRole("link", { name: "Skip to main content" }).press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
});
