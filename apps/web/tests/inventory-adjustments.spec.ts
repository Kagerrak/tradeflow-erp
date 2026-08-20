import { expect, test } from "@playwright/test";

const adjustment = {
  adjustment_id: "44444444-4444-4444-4444-444444444444",
  base_currency: "PHP",
  kind: "surplus",
  location_id: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
  lot_code: null,
  posted_at: null,
  posted_by: null,
  posted_movement_group_id: null,
  quantity_base: "5.000000",
  reason: "Count correction.",
  requested_at: "2026-08-20T00:00:00Z",
  requested_by: "adjuster-mnl",
  reversal_movement_group_id: null,
  reversal_reason: null,
  reversed_at: null,
  reversed_by: null,
  sku_id: "d6a72680-6334-434d-8969-d2fc87da6397",
  source_reference: "COUNT-001",
  status: "pending_authorization",
  unit_cost: "10.000000",
  value_delta: "50.000000",
  version: 1,
  warehouse_id: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
};

test("requests, posts, and reverses an adjustment through the workspace", async ({
  page,
}) => {
  let adjustments: unknown[] = [];
  await page.route("**/api/inventory/adjustments", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        json: { items: adjustments, total: adjustments.length },
      });
      return;
    }
    const body = (await route.request().postDataJSON()) as {
      skuId: string;
      warehouseId: string;
      locationId: string;
      kind: string;
      quantity: string;
      reason: string;
      sourceReference: string;
    };
    const created = {
      ...adjustment,
      sku_id: body.skuId,
      warehouse_id: body.warehouseId,
      location_id: body.locationId,
      kind: body.kind,
      quantity_base: body.quantity,
      reason: body.reason,
      source_reference: body.sourceReference,
    };
    adjustments = [created];
    await route.fulfill({
      contentType: "application/json",
      json: { adjustment: created },
      status: 201,
    });
  });
  await page.route("**/api/inventory/adjustments/*/post", async (route) => {
    const posted = { ...adjustment, status: "posted", version: 2 };
    adjustments = [posted];
    await route.fulfill({
      contentType: "application/json",
      json: { adjustment: posted },
      status: 201,
    });
  });
  await page.route("**/api/inventory/adjustments/*/reverse", async (route) => {
    const reversed = { ...adjustment, status: "reversed", version: 3 };
    adjustments = [reversed];
    await route.fulfill({
      contentType: "application/json",
      json: { adjustment: reversed },
      status: 201,
    });
  });

  await page.goto("/inventory/adjustments");
  await expect(
    page.getByRole("heading", {
      name: "Correct inventory counts with authorized adjustments.",
    }),
  ).toBeVisible();

  await page
    .getByTestId("adjustment-sku-id")
    .fill("d6a72680-6334-434d-8969-d2fc87da6397");
  await page
    .getByTestId("adjustment-warehouse")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page
    .getByTestId("adjustment-location")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page.getByTestId("adjustment-quantity").fill("5");
  await page.getByTestId("adjustment-reason").fill("Count correction.");
  await page.getByTestId("adjustment-source-reference").fill("COUNT-001");
  await page.getByTestId("adjustment-request").click();

  await expect(page.getByTestId("adjustment-message")).toContainText(
    "pending_authorization",
  );
  await expect(page.getByText("5.000000")).toBeVisible();

  await page.getByTestId(`adjustment-post-${adjustment.adjustment_id}`).click();
  await expect(page.getByTestId("adjustment-message")).toContainText("posted");

  await page
    .getByTestId(`adjustment-reverse-${adjustment.adjustment_id}`)
    .click();
  await page.getByRole("dialog").locator("input").fill("Recount corrected.");
  await page.getByRole("dialog").getByText("OK").click();
  await expect(page.getByTestId("adjustment-message")).toContainText(
    "reversed",
  );
});

test("shows scope denial as a workspace message", async ({ page }) => {
  await page.route("**/api/inventory/adjustments", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        json: { items: [], total: 0 },
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      json: {
        code: "operational_scope_required",
        correlationId: "scope-denied",
        kind: "forbidden",
        message: "Adjustment is outside your warehouse scope.",
      },
      status: 403,
    });
  });

  await page.goto("/inventory/adjustments");
  await page
    .getByTestId("adjustment-sku-id")
    .fill("d6a72680-6334-434d-8969-d2fc87da6397");
  await page
    .getByTestId("adjustment-warehouse")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page
    .getByTestId("adjustment-location")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page.getByTestId("adjustment-quantity").fill("5");
  await page.getByTestId("adjustment-reason").fill("Count correction.");
  await page.getByTestId("adjustment-source-reference").fill("COUNT-001");
  await page.getByTestId("adjustment-request").click();

  await expect(page.getByTestId("adjustment-message")).toContainText(
    "rejected",
  );
});

test("offers recovery when the adjustment service is unavailable", async ({
  page,
}) => {
  await page.route("**/api/inventory/adjustments", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { correlationId: "adjustment-outage", kind: "unavailable" },
      status: 503,
    });
  });

  await page.goto("/inventory/adjustments");
  await expect(page.getByText("adjustment-outage")).toBeVisible();
  await page.getByRole("button", { name: "Retry adjustments" }).click();
  await expect(page.getByText("adjustment-outage")).toBeVisible();
});
