import { expect, test } from "@playwright/test";

const fulfillmentOrderId = "765b5ab6-7f39-4671-8561-747755641016";
const lineId = "4af0c99a-b55d-4f68-bf34-6f0805630032";
const pickId = "6dfdb618-3d36-4597-aa35-a3ff46fa8aa0";
const priorPickId = "079b17ff-1456-459d-a10c-b73cd665d11c";

test("dispatches staged custody to one Delivery Staff assignment", async ({
  page,
}) => {
  let dispatchBody: Record<string, unknown> | undefined;
  await page.route(`**/api/picking/${fulfillmentOrderId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        context: {
          fulfillmentOrderId,
          lines: [
            {
              baseStockingUnit: "EA",
              expirationControl: false,
              fefoCandidates: [],
              lineId,
              pickedQuantityBase: "6.000000",
              releasedQuantityBase: "12.000000",
              remainingQuantityBase: "6.000000",
              reversedQuantityBase: "0.000000",
              skuCode: "JUICE-1L",
              skuId: "37989314-b1b7-4bea-9c68-b6390ddae80f",
              skuName: "Mango Juice 1L",
              trackingPolicy: "untracked",
            },
          ],
          status: "partially_picked",
          version: 4,
          warehouseId: "dd2cabf2-3f01-4a5c-94a8-b1a580b1d0f4",
        },
        correlationId: "dispatch-context",
        kind: "ready",
      },
    }),
  );
  await page.route(`**/api/picking/${fulfillmentOrderId}/picks`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "pick-list",
        items: [
          {
            actorSubject: "warehouse-picker-mnl",
            correlationId: "prior-dispatch",
            dispatched: true,
            eventType: "posted",
            lines: [],
            pickId: priorPickId,
            postedAt: "2026-08-01T08:30:00Z",
            quantityBase: "1.000000",
            reason: null,
            reversalOfPickId: null,
          },
          {
            actorSubject: "warehouse-picker-mnl",
            correlationId: "picked",
            dispatched: false,
            eventType: "posted",
            lines: [
              {
                conversionSnapshot: {},
                lineId,
                lotSelections: [],
                quantityBase: "6.000000",
                serialSelections: [],
                skuId: "37989314-b1b7-4bea-9c68-b6390ddae80f",
                sourceMovementId: "source-movement",
                stagingMovementId: "staging-movement",
              },
            ],
            pickId,
            postedAt: "2026-08-01T09:00:00Z",
            quantityBase: "6.000000",
            reason: null,
            reversalOfPickId: null,
          },
        ],
        kind: "ready",
        total: 1,
      },
    }),
  );
  await page.route(`**/api/dispatch/${fulfillmentOrderId}`, async (route) => {
    dispatchBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "delivery-dispatched",
        delivery: {
          assignedTo: "delivery-mnl",
          deliveryId: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
          fulfillmentOrderId,
          lines: [],
          paymentTimingPolicy: "cash_on_delivery",
          status: "dispatched",
          version: 1,
        },
        kind: "dispatched",
      },
    });
  });

  await page.goto(`/dispatch?fulfillmentOrderId=${fulfillmentOrderId}`);
  await expect(
    page.getByRole("heading", { name: "Release custody, assign the run." }),
  ).toBeVisible();
  await expect(
    page.getByText("Partially staged", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Dispatch Staging", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("In Transit", { exact: true })).toBeVisible();
  await page.getByLabel("Delivery Staff subject").fill("delivery-mnl");
  await page.getByRole("button", { name: "Dispatch selected Pick" }).click();
  await expect(
    page.getByRole("heading", { name: "Custody acknowledged in transit" }),
  ).toBeVisible();
  await expect(page.getByText("delivery-mnl", { exact: true })).toBeVisible();
  const command = dispatchBody?.["command"] as Record<string, unknown>;
  expect(command["pick_ids"]).toEqual([pickId]);
  expect(command["expected_fulfillment_version"]).toBe(4);
  expect(dispatchBody?.["idempotencyKey"]).toEqual(expect.any(String));
});

test("renders forbidden Dispatch authority explicitly", async ({ page }) => {
  await page.route(`**/api/picking/${fulfillmentOrderId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        code: "operational_scope_required",
        correlationId: "dispatch-forbidden",
        kind: "forbidden",
        message: "Warehouse Operational Scope is required for Dispatch.",
      },
      status: 403,
    }),
  );
  await page.route(`**/api/picking/${fulfillmentOrderId}/picks`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { correlationId: "pick-list", items: [], kind: "ready", total: 0 },
    }),
  );
  await page.goto(`/dispatch?fulfillmentOrderId=${fulfillmentOrderId}`);
  await expect(
    page.getByRole("heading", { name: "Dispatch authority required" }),
  ).toBeVisible();
  await expect(page.getByText("operational_scope_required")).toBeVisible();
});

test("retains an uncertain Dispatch command unchanged before surfacing conflict", async ({
  page,
}) => {
  const commandKeys: string[] = [];
  let attempts = 0;
  await page.route(`**/api/picking/${fulfillmentOrderId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        context: {
          fulfillmentOrderId,
          lines: [],
          status: "picked",
          version: 4,
          warehouseId: "dd2cabf2-3f01-4a5c-94a8-b1a580b1d0f4",
        },
        correlationId: "dispatch-context",
        kind: "ready",
      },
    }),
  );
  await page.route(`**/api/picking/${fulfillmentOrderId}/picks`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "pick-list",
        items: [
          {
            actorSubject: "warehouse-picker-mnl",
            correlationId: "picked",
            dispatched: false,
            eventType: "posted",
            lines: [],
            pickId,
            postedAt: "2026-08-01T09:00:00Z",
            quantityBase: "6.000000",
            reason: null,
            reversalOfPickId: null,
          },
        ],
        kind: "ready",
        total: 1,
      },
    }),
  );
  await page.route(`**/api/dispatch/${fulfillmentOrderId}`, async (route) => {
    const body = route.request().postDataJSON() as { idempotencyKey: string };
    commandKeys.push(body.idempotencyKey);
    attempts += 1;
    await route.fulfill({
      contentType: "application/json",
      json:
        attempts === 1
          ? {
              code: "delivery_service_unavailable",
              correlationId: "uncertain-dispatch",
              kind: "unavailable",
              message: "The Dispatch outcome is uncertain.",
            }
          : {
              code: "fulfillment_version_conflict",
              correlationId: "dispatch-conflict",
              kind: "conflict",
              message: "The Fulfillment Order changed.",
            },
      status: attempts === 1 ? 503 : 409,
    });
  });

  await page.goto(`/dispatch?fulfillmentOrderId=${fulfillmentOrderId}`);
  await page.getByLabel("Delivery Staff subject").fill("delivery-mnl");
  await page.getByRole("button", { name: "Dispatch selected Pick" }).click();
  await expect(
    page.getByRole("heading", { name: "Safe retry retained" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry unchanged dispatch" }).click();
  await expect(
    page.getByRole("heading", { name: "Authoritative dispatch changed" }),
  ).toBeVisible();
  expect(commandKeys).toHaveLength(2);
  expect(commandKeys[0]).toBe(commandKeys[1]);
});
