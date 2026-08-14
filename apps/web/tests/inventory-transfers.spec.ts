import { expect, test } from "@playwright/test";

const transfer = {
  base_currency: "PHP",
  from_location_id: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
  from_warehouse_id: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
  lot_code: null,
  quantity_base: "10.000000",
  reason: "Replenishment.",
  release_movement_group_id: "11111111-1111-1111-1111-111111111111",
  requested_at: "2026-08-14T00:00:00Z",
  requested_by: "warehouse-clerk",
  sku_id: "d6a72680-6334-434d-8969-d2fc87da6397",
  source_reference: "REPL-001",
  status: "released",
  to_location_id: "22222222-2222-2222-2222-222222222222",
  to_warehouse_id: "22222222-2222-2222-2222-222222222222",
  transfer_id: "33333333-3333-3333-3333-333333333333",
  unit_cost: "12.000000",
};

test("requests and receives a transfer through the workspace", async ({
  page,
}) => {
  let transfers: unknown[] = [];
  await page.route("**/api/inventory/transfers", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        json: { items: transfers, total: transfers.length },
      });
      return;
    }
    const body = (await route.request().postDataJSON()) as {
      fromWarehouseId: string;
      fromLocationId: string;
      quantity: string;
      reason: string;
      skuId: string;
      sourceReference: string;
      toWarehouseId: string;
      toLocationId: string;
    };
    const created = {
      ...transfer,
      from_warehouse_id: body.fromWarehouseId,
      from_location_id: body.fromLocationId,
      quantity_base: body.quantity,
      reason: body.reason,
      sku_id: body.skuId,
      source_reference: body.sourceReference,
      to_warehouse_id: body.toWarehouseId,
      to_location_id: body.toLocationId,
    };
    transfers = [created];
    await route.fulfill({
      contentType: "application/json",
      json: { transfer: created },
      status: 201,
    });
  });
  await page.route("**/api/inventory/transfers/*/receive", async (route) => {
    const received = { ...transfer, status: "received" };
    transfers = [received];
    await route.fulfill({
      contentType: "application/json",
      json: { transfer: received },
      status: 201,
    });
  });

  await page.goto("/inventory/transfers");
  await expect(
    page.getByRole("heading", {
      name: "Move stock between warehouses at source cost.",
    }),
  ).toBeVisible();

  await page
    .getByTestId("transfer-sku-id")
    .fill("d6a72680-6334-434d-8969-d2fc87da6397");
  await page
    .getByTestId("transfer-from-warehouse")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page
    .getByTestId("transfer-to-warehouse")
    .fill("22222222-2222-2222-2222-222222222222");
  await page
    .getByTestId("transfer-from-location")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page
    .getByTestId("transfer-to-location")
    .fill("22222222-2222-2222-2222-222222222222");
  await page.getByTestId("transfer-quantity").fill("10");
  await page.getByTestId("transfer-reason").fill("Replenishment.");
  await page.getByTestId("transfer-source-reference").fill("REPL-001");
  await page.getByTestId("transfer-request").click();

  await expect(page.getByTestId("transfer-message")).toContainText("released");
  await expect(page.getByText("10")).toBeVisible();
  await page.getByTestId(`transfer-receive-${transfer.transfer_id}`).click();
  await expect(page.getByTestId("transfer-message")).toContainText("received");
});

test("shows scope denial as a workspace message", async ({ page }) => {
  await page.route("**/api/inventory/transfers", async (route) => {
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
        code: "transfer_forbidden",
        correlationId: "scope-denied",
        kind: "forbidden",
        message: "Transfer is outside your warehouse scope.",
      },
      status: 403,
    });
  });

  await page.goto("/inventory/transfers");
  await page
    .getByTestId("transfer-sku-id")
    .fill("d6a72680-6334-434d-8969-d2fc87da6397");
  await page
    .getByTestId("transfer-from-warehouse")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page
    .getByTestId("transfer-to-warehouse")
    .fill("22222222-2222-2222-2222-222222222222");
  await page
    .getByTestId("transfer-from-location")
    .fill("6cadf528-a2ff-4d05-b25c-940c79b112ad");
  await page
    .getByTestId("transfer-to-location")
    .fill("22222222-2222-2222-2222-222222222222");
  await page.getByTestId("transfer-quantity").fill("10");
  await page.getByTestId("transfer-reason").fill("Replenishment.");
  await page.getByTestId("transfer-source-reference").fill("REPL-001");
  await page.getByTestId("transfer-request").click();

  await expect(page.getByTestId("transfer-message")).toContainText("rejected");
});

test("offers recovery when the transfer service is unavailable", async ({
  page,
}) => {
  await page.route("**/api/inventory/transfers", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { correlationId: "transfer-outage", kind: "unavailable" },
      status: 503,
    });
  });

  await page.goto("/inventory/transfers");
  await expect(page.getByText("transfer-outage")).toBeVisible();
  await page.getByRole("button", { name: "Retry transfers" }).click();
  await expect(page.getByText("transfer-outage")).toBeVisible();
});
