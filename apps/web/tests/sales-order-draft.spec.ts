import { expect, test } from "@playwright/test";

const branchId = "efad4205-5060-49fb-b752-3faca649ca6e";
const customerId = "98481a1c-e493-41a6-851b-93142553ceab";
const addressId = "4d8ad09a-f96f-41b3-b30a-0af843353943";
const itemId = "d60c173e-efec-4b3a-b1c6-1e893e4cdfff";
const warehouseId = "02efc423-72ca-48dc-82a8-700566ffbd90";
const alternateWarehouseId = "a313bdfb-a62c-41ea-8548-a8023fb01084";

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
        capabilities: ["sales:commercial-approve", "sales:order-write"],
        user: { display_name: "Manila Sales", subject: "sales-mnl" },
        warehouses: [
          {
            branch_id: branchId,
            code: "MNL-01",
            is_active: true,
            name: "Manila DC",
            warehouse_id: warehouseId,
          },
          {
            branch_id: branchId,
            code: "MNL-02",
            is_active: true,
            name: "Manila Overflow",
            warehouse_id: alternateWarehouseId,
          },
        ],
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

test("shows partial reservation and exception-required Commercial Approval states", async ({
  page,
}) => {
  await routeWorkspace(page);
  await page.route("**/api/sales-orders", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "saved-order",
        draft: savedDraft,
        kind: "saved",
      },
    }),
  );
  let attempts = 0;
  const approvalKeys: string[] = [];
  await page.route(
    `**/api/sales-orders/${savedDraft.salesOrderId}/commercial-approval`,
    (route) => {
      attempts += 1;
      approvalKeys.push(
        (
          route.request().postDataJSON() as {
            idempotencyKey: string;
          }
        ).idempotencyKey,
      );
      if (attempts === 1) {
        return route.fulfill({
          contentType: "application/json",
          json: {
            correlationId: "exception-required",
            kind: "exception_required",
          },
          status: 409,
        });
      }
      if (attempts === 2) return route.abort("failed");
      return route.fulfill({
        contentType: "application/json",
        json: {
          approval: {
            approvalId: "9ee0c3c0-d673-452f-bdef-eeec91a4773f",
            approvedBy: "commercial-mnl",
            backorderQuantityBase: "1.000000",
            credit: {
              approvedExcess: "0.00",
              approvedUninvoicedBefore: "0.00",
              creditLimit: null,
              openBalance: "0.00",
              orderValue: "0.00",
              overrideRequired: false,
              projectedExposure: "0.00",
            },
            makerSubject: "sales-mnl",
            requiredExceptions: ["discount"],
            reservations: [],
            reservedQuantityBase: "2.000000",
            salesOrderId: savedDraft.salesOrderId,
            salesOrderRevisionId: "be85cc1b-699f-4567-b833-a66944b2d8a6",
            status: "approved",
            warehouseId,
          },
          correlationId: "approved",
          kind: "approved",
        },
      });
    },
  );
  await page.goto("/sales-orders/new");
  await page.getByLabel("Customer Account").selectOption(customerId);
  await page.getByLabel("COLA-330 quantity").fill("3.000000");
  await page.getByRole("button", { name: "Save Sales Order Draft" }).click();
  await page.getByRole("button", { name: "Commercially approve" }).click();
  await expect(
    page.getByRole("heading", {
      name: "A different eligible approver is required",
    }),
  ).toBeVisible();
  await page.getByLabel("Commercial exception reason").fill("Reviewed");
  await page.getByRole("button", { name: "Commercially approve" }).click();
  await expect(
    page.getByRole("heading", { name: "Commercial controls are unavailable" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Commercially approve" }).click();
  await expect(
    page.getByRole("heading", { name: "Partially reserved" }),
  ).toBeVisible();
  await expect(page.getByText("1.000000 on backorder")).toBeVisible();
  expect(approvalKeys[2]).toBe(approvalKeys[1]);
});

test("guides a checker through approval failures before approving a pending revision", async ({
  page,
}) => {
  await page.route("**/api/customer-scope", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        capabilities: ["sales:commercial-approve", "sales:order-read"],
        user: { display_name: "Commercial Manager" },
        warehouses: [
          {
            branch_id: branchId,
            code: "MNL-01",
            is_active: true,
            name: "Manila DC",
            warehouse_id: warehouseId,
          },
          {
            branch_id: branchId,
            code: "MNL-02",
            is_active: true,
            name: "Manila Overflow",
            warehouse_id: alternateWarehouseId,
          },
        ],
      },
    }),
  );
  await page.route("**/api/sales-orders?query=", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "pending-orders",
        items: [
          {
            branchId,
            currency: "PHP",
            customerId,
            customerName: "Draft Order Retail",
            grandTotal: "31.89",
            paymentTimingPolicy: "prepaid",
            salesOrderId: savedDraft.salesOrderId,
            status: "draft",
            version: 1,
          },
        ],
        kind: "ready",
        total: 1,
      },
    }),
  );
  await page.route(`**/api/sales-orders/${savedDraft.salesOrderId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "loaded-order",
        draft: savedDraft,
        kind: "loaded",
      },
    }),
  );
  await page.route(
    `**/api/sales-orders/${savedDraft.salesOrderId}/commercial-review?**`,
    (route) => {
      const selectedWarehouse = new URL(route.request().url()).searchParams.get(
        "warehouse_id",
      );
      const alternate = selectedWarehouse === alternateWarehouseId;
      return route.fulfill({
        contentType: "application/json",
        json: {
          correlationId: "commercial-review",
          kind: "ready",
          review: {
            approvedUninvoiced: "250.00",
            creditHold: false,
            creditLimit: "1400.00",
            currency: "PHP",
            customerAccountNumber: "MNL-SALES-001",
            customerId,
            customerName: "Draft Order Retail",
            customerSnapshotCurrent: true,
            customerStatus: "active",
            discountTotal: "0.03",
            grandTotal: "31.89",
            lines: [
              {
                allocatedDiscount: "0.03",
                backorderQuantityBase: alternate ? "0.000000" : "1.000000",
                belowFloor: true,
                calculationSnapshot: {
                  line_total: "31.89",
                  subtotal: "28.50",
                },
                conversionSnapshot: {
                  base_quantity_per_unit: "1.000000",
                  base_stocking_unit: "EA",
                  entered_unit: "EA",
                },
                effectiveUnitPrice: "9.500000",
                enteredQuantity: "3.000000",
                enteredUnit: "EA",
                floorUnitPrice: "10.000000",
                lineId: savedDraft.lines[0]!.lineId,
                listUnitPrice: "9.500000",
                manualOverrideUnitPrice: null,
                quantityBase: "3.000000",
                reservableQuantityBase: alternate ? "3.000000" : "2.000000",
                skuCode: "COLA-330",
                skuId: "4d209f00-0c57-49fc-9f0b-fc5cf082cb02",
                skuName: "Cola 330 SKU",
                taxSnapshot: {
                  inclusion_mode: "exclusive",
                  tax_code: "VAT12",
                  tax_rate: "0.120000",
                },
                warehouseOnHandBase: alternate ? "20.000000" : "10.000000",
                warehouseReservedBase: alternate ? "4.000000" : "8.000000",
              },
            ],
            makerSubject: "sales-mnl",
            openBalance: "1100.00",
            paymentTerms: "Net 30",
            paymentTimingPolicy: "prepaid",
            projectedExposure: "1350.00",
            requiredExceptions: [
              {
                amount: "0.03",
                percentage: "0.105263",
                type: "discount",
              },
              {
                amount: "1.50",
                percentage: null,
                type: "below_floor",
              },
            ],
            salesOrderId: savedDraft.salesOrderId,
            salesOrderRevisionId: "be85cc1b-699f-4567-b833-a66944b2d8a6",
            status: "draft",
            subtotal: "28.50",
            taxTotal: "3.42",
            version: 1,
            warehouseId: selectedWarehouse,
          },
        },
      });
    },
  );
  const failureStates = [
    {
      correlationId: "approval-forbidden",
      errorCode: "branch_scope_forbidden",
      kind: "forbidden",
      message: "This checker is not assigned to the order's Branch.",
    },
    {
      correlationId: "approval-conflict",
      errorCode: "sales_order_version_conflict",
      kind: "conflict",
    },
    {
      correlationId: "approval-validation",
      errorCode: "exception_reason_required",
      kind: "validation",
    },
    {
      correlationId: "approval-unauthenticated",
      kind: "unauthenticated",
    },
    {
      correlationId: "approval-unavailable",
      kind: "unavailable",
    },
  ] as const;
  let approvalAttempt = 0;
  await page.route(
    `**/api/sales-orders/${savedDraft.salesOrderId}/commercial-approval`,
    (route) => {
      const failure = failureStates[approvalAttempt];
      approvalAttempt += 1;
      return failure === undefined
        ? route.fulfill({
            contentType: "application/json",
            json: {
              approval: {
                approvalId: "9ee0c3c0-d673-452f-bdef-eeec91a4773f",
                approvedBy: "commercial-mnl",
                backorderQuantityBase: "0.000000",
                credit: {
                  approvedExcess: "0.00",
                  approvedUninvoicedBefore: "0.00",
                  creditLimit: null,
                  openBalance: "0.00",
                  orderValue: "0.00",
                  overrideRequired: false,
                  projectedExposure: "0.00",
                },
                makerSubject: "sales-mnl",
                requiredExceptions: ["discount"],
                reservations: [],
                reservedQuantityBase: "3.000000",
                salesOrderId: savedDraft.salesOrderId,
                salesOrderRevisionId: "be85cc1b-699f-4567-b833-a66944b2d8a6",
                status: "approved",
                warehouseId,
              },
              correlationId: "approved",
              kind: "approved",
            },
          })
        : route.fulfill({
            contentType: "application/json",
            json: failure,
          });
    },
  );
  await page.goto("/sales-orders/approvals");
  await page
    .getByRole("button", { name: /Draft Order Retail PHP 31.89/ })
    .click();
  await expect(page.getByRole("heading", { name: "PHP 31.89" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Draft Order Retail" }),
  ).toBeVisible();
  await expect(
    page.getByText("MNL-SALES-001 · active · Net 30 · prepaid"),
  ).toBeVisible();
  await expect(page.getByText("Maker sales-mnl")).toBeVisible();
  await expect(page.getByText("No credit hold")).toBeVisible();
  await expect(
    page.getByText("Projected exposure").locator(".."),
  ).toContainText("PHP 1350.00");
  await expect(
    page.getByRole("listitem").filter({ hasText: "below floor" }),
  ).toContainText("PHP 1.50");
  const stock = page.locator(
    'dl[aria-label="COLA-330 warehouse availability"]',
  );
  await expect(stock).toContainText("On hand10.000000");
  await expect(stock).toContainText("Reserved8.000000");
  await expect(stock).toContainText("Reservable2.000000");
  await expect(stock).toContainText("Backorder1.000000");
  await page
    .getByLabel("Fulfillment warehouse")
    .selectOption(alternateWarehouseId);
  await expect(stock).toContainText("On hand20.000000");
  await expect(stock).toContainText("Reserved4.000000");
  await expect(stock).toContainText("Reservable3.000000");
  await expect(stock).toContainText("Backorder0.000000");
  await expect(page.getByText("3.000000 EA = 3.000000 EA")).toBeVisible();
  await expect(
    page.getByText("List 9.500000 · Effective 9.500000 · Floor 10.000000"),
  ).toBeVisible();
  await expect(page.getByText("VAT12 · 0.120000 · exclusive")).toBeVisible();
  await page.getByLabel("Commercial exception reason").fill("Reviewed");
  const failureExpectations = [
    {
      errorCode: "branch_scope_forbidden",
      guidance:
        "Ask an administrator to assign Commercial Approval access for this Branch and Warehouse.",
      serverMessage: "This checker is not assigned to the order's Branch.",
      title: "Commercial Approval access is not assigned",
    },
    {
      guidance:
        "Reload the authoritative order and review its latest revision before approving.",
      title: "The priced revision changed",
    },
    {
      guidance:
        "Review the Warehouse and required exception reasons, then retry this exact revision.",
      title: "Approval evidence needs correction",
    },
    {
      guidance: "Sign in again, then reopen the pending revision.",
      title: "Sign in to approve",
    },
    {
      guidance:
        "Keep this revision unchanged and retry. The same approval command identity will be reused.",
      title: "Commercial controls are unavailable",
    },
  ] as const;
  for (const expectation of failureExpectations) {
    await page.getByRole("button", { name: "Approve exact revision" }).click();
    await expect(
      page.getByRole("heading", { name: expectation.title }),
    ).toBeVisible();
    await expect(page.getByText(expectation.guidance)).toBeVisible();
    if ("serverMessage" in expectation) {
      await expect(page.getByText(expectation.serverMessage)).toBeVisible();
      await expect(page.getByText(expectation.errorCode)).toBeVisible();
    }
  }
  await page.getByRole("button", { name: "Approve exact revision" }).click();
  await expect(
    page.getByRole("heading", { name: "Approved and fully reserved" }),
  ).toBeVisible();
});
