import { expect, test } from "@playwright/test";

const branchId = "efad4205-5060-49fb-b752-3faca649ca6e";
const customerId = "98481a1c-e493-41a6-851b-93142553ceab";
const addressId = "4d8ad09a-f96f-41b3-b30a-0af843353943";
const itemId = "d60c173e-efec-4b3a-b1c6-1e893e4cdfff";

async function routeWorkspace(page: import("@playwright/test").Page) {
  await page.route("**/api/customer-scope", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        branches: [
          {
            branch_id: branchId,
            code: "MNL",
            is_active: true,
            name: "Manila",
          },
        ],
        capabilities: ["sales:order-write"],
        user: { display_name: "Manila Sales", subject: "sales-mnl" },
      },
    }),
  );
  await page.route("**/api/customers?query=", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "customers",
        items: [
          {
            accountNumber: "MNL-SALES-001",
            branchId,
            creditHold: false,
            customerId,
            legalName: "Draft Order Retail",
            paymentTimingPolicy: "prepaid",
            status: "active",
            version: 1,
          },
        ],
        kind: "ready",
        total: 1,
      },
    }),
  );
  await page.route("**/api/sales-orders/reference?*", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "reference",
        kind: "ready",
        reference: {
          addresses: [
            {
              addressKey: "DELIVERY",
              addressVersionId: addressId,
              city: "Manila",
              line1: "100 Draft Street",
              version: 1,
            },
          ],
          branchId,
          currency: "PHP",
          customerId,
          customerName: "Draft Order Retail",
          customerVersion: 1,
          items: [
            {
              baseQuantityPerUnit: "1.000000",
              baseStockingUnit: "EA",
              floorUnitPrice: "7.500000",
              listUnitPrice: "9.500000",
              priceListLineId: itemId,
              skuCode: "COLA-330",
              skuId: "4d209f00-0c57-49fc-9f0b-fc5cf082cb02",
              skuName: "Cola 330 SKU",
              taxCode: "VAT12",
              taxRate: "0.120000",
              unitCode: "EA",
            },
          ],
          paymentTimingDefault: "prepaid",
          priceInclusionMode: "exclusive",
          priceListCode: "MNL-CUSTOMER",
          priceListVersion: 1,
          priceListVersionId: "2903b3b0-608f-4caf-907a-0dd0886bb8f7",
          pricingDate: "2026-07-29",
        },
      },
    }),
  );
}

const savedDraft = {
  branchId,
  currency: "PHP",
  customerId,
  customerVersion: 1,
  deliveryAddressLine: "100 Draft Street",
  discountTotal: "0.03",
  grandTotal: "31.89",
  lines: [
    {
      allocatedDiscount: "0.03",
      enteredQuantity: "3.000000",
      enteredUnit: "EA",
      lineId: "a5551b35-34ff-4cb8-8062-d6386f7e4e25",
      linePosition: 1,
      lineTotal: "31.89",
      listUnitPrice: "9.500000",
      priceListCode: "MNL-CUSTOMER",
      priceSource: "customer",
      skuCode: "COLA-330",
      skuName: "Cola 330 SKU",
      taxAmount: "3.42",
    },
  ],
  paymentTimingOverrideReason: null,
  paymentTimingPolicy: "prepaid",
  priceInclusionMode: "exclusive",
  priceListCode: "MNL-CUSTOMER",
  salesOrderId: "323484f7-f3b5-4070-846f-83b9aad4fadb",
  status: "draft",
  subtotal: "28.50",
  taxTotal: "3.42",
  version: 1,
};

test("creates and edits an authoritative priced Sales Order Draft", async ({
  page,
}) => {
  await routeWorkspace(page);
  let createBody: Record<string, unknown> | undefined;
  let createCount = 0;
  let updateCount = 0;
  await page.route("**/api/sales-orders", async (route) => {
    createCount += 1;
    createBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "saved-order",
        draft: savedDraft,
        kind: "saved",
      },
    });
  });
  await page.route(
    `**/api/sales-orders/${savedDraft.salesOrderId}`,
    (route) => {
      updateCount += 1;
      return route.fulfill({
        contentType: "application/json",
        json: {
          correlationId: "order-conflict",
          kind: "conflict",
        },
        status: 409,
      });
    },
  );
  await page.goto("/sales-orders/new");
  await expect(
    page.getByRole("heading", {
      name: "Price the promise before committing it.",
    }),
  ).toBeVisible();
  await page.getByLabel("Customer Account").selectOption(customerId);
  await expect(page.getByText(/MNL-CUSTOMER/)).toBeVisible();
  await page.getByLabel("COLA-330 quantity").fill("3.000000");
  await page.getByLabel("Order discount").fill("0.03");
  await page.getByRole("button", { name: "Save Sales Order Draft" }).click();
  await expect(
    page.getByRole("heading", { name: "Draft acknowledged by TradeFlow" }),
  ).toBeVisible();
  await expect(page.getByText("PHP 31.89")).toBeVisible();
  expect(createBody?.["idempotencyKey"]).toEqual(expect.any(String));

  await page.getByLabel("COLA-330 quantity").fill("4.000000");
  await page.getByRole("button", { name: "Save new draft revision" }).click();
  await expect(
    page.getByRole("heading", {
      name: "Server state changed — review required",
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save new draft revision" }).click();
  await expect.poll(() => updateCount).toBe(2);
  expect(createCount).toBe(1);
});

test("shows forbidden and unavailable order-entry boundaries", async ({
  page,
}) => {
  await page.route("**/api/customer-scope", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { correlationId: "sales-forbidden", kind: "forbidden" },
      status: 403,
    }),
  );
  await page.route("**/api/customers?query=", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { correlationId: "customers-forbidden", kind: "forbidden" },
      status: 403,
    }),
  );
  await page.goto("/sales-orders/new");
  await expect(
    page.getByRole("heading", { name: "Sales access is not assigned" }),
  ).toBeVisible();
  await expect(page.getByText("sales-forbidden")).toBeVisible();
});
