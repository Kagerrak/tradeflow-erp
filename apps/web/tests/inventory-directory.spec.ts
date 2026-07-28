import { expect, test } from "@playwright/test";

const item = {
  available: "30.000000",
  baseCurrency: "PHP",
  baseStockingUnit: "EA",
  custody: "available",
  expirationControl: true,
  expirationDate: "2027-12-31",
  warehouseInventoryValue: "360.000000",
  locationCode: "AVAILABLE",
  lotCode: "LOT-A",
  movingAverageUnitCost: "12.000000",
  onHand: "30.000000",
  reserved: "0.000000",
  serialNumbers: [],
  skuCode: "COLA-330",
  skuId: "d6a72680-6334-434d-8969-d2fc87da6397",
  skuName: "Cola 330 mL",
  trackingPolicy: "lot",
  warehouseCode: "MNL-01",
  warehouseId: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
};

test("shows scoped inventory quantity, custody, and traceability", async ({
  page,
}) => {
  await page.route("**/api/inventory?query=*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "inventory-ready",
        items: [item],
        kind: "ready",
        total: 1,
      },
    });
  });
  await page.goto("/inventory");

  await expect(
    page.getByRole("heading", {
      name: "Promise only what is actually available.",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Cola 330 mL" }),
  ).toBeVisible();
  await expect(page.getByText("30.000000 EA")).toBeVisible();
  await expect(page.getByText("MNL-01 / AVAILABLE")).toBeVisible();
  await expect(page.getByText("LOT-A / 2027-12-31")).toBeVisible();
  await expect(page.getByText("PHP 12.000000")).toBeVisible();
  await expect(page.getByText("PHP 360.000000")).toBeVisible();

  await page.getByLabel("Search SKU code or product name").fill("COLA");
  await page.getByRole("button", { name: "Search stock" }).click();
  await expect(
    page.getByRole("heading", { name: "Cola 330 mL" }),
  ).toBeVisible();
});

test("renders empty and forbidden states without fabricated stock", async ({
  page,
}) => {
  let forbidden = false;
  await page.route("**/api/inventory?query=*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: forbidden
        ? {
            correlationId: "inventory-forbidden",
            kind: "forbidden",
          }
        : {
            correlationId: "inventory-empty",
            items: [],
            kind: "ready",
            total: 0,
          },
      status: forbidden ? 403 : 200,
    });
  });
  await page.goto("/inventory");
  await expect(
    page.getByRole("heading", { name: "No stock in your Warehouse scope" }),
  ).toBeVisible();
  forbidden = true;
  await page.getByRole("button", { name: "Search stock" }).click();
  await expect(
    page.getByRole("heading", { name: "Inventory access is not assigned" }),
  ).toBeVisible();
  await expect(page.getByText("inventory-forbidden")).toBeVisible();
});

test("offers recovery with a stable support reference", async ({ page }) => {
  let unavailable = true;
  await page.route("**/api/inventory?query=*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: unavailable
        ? { correlationId: "inventory-outage", kind: "unavailable" }
        : {
            correlationId: "inventory-recovered",
            items: [item],
            kind: "ready",
            total: 1,
          },
      status: unavailable ? 503 : 200,
    });
  });
  await page.goto("/inventory");
  await expect(page.getByText("inventory-outage")).toBeVisible();
  unavailable = false;
  await page.getByRole("button", { name: "Retry availability" }).click();
  await expect(
    page.getByRole("heading", { name: "Cola 330 mL" }),
  ).toBeVisible();
});
