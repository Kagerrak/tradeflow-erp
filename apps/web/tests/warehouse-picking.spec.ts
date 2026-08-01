import { expect, test } from "@playwright/test";

const fulfillmentOrderId = "765b5ab6-7f39-4671-8561-747755641016";
const lineId = "4af0c99a-b55d-4f68-bf34-6f0805630032";
const pickId = "6dfdb618-3d36-4597-aa35-a3ff46fa8aa0";

const context = {
  fulfillmentOrderId,
  lines: [
    {
      baseStockingUnit: "EA",
      expirationControl: true,
      fefoCandidates: [
        {
          availableQuantityBase: "18.000000",
          expirationDate: "2026-08-20",
          lotCode: "LOT-EARLY",
          recommended: true,
        },
        {
          availableQuantityBase: "30.000000",
          expirationDate: "2026-09-15",
          lotCode: "LOT-LATE",
          recommended: false,
        },
      ],
      lineId,
      pickedQuantityBase: "6.000000",
      releasedQuantityBase: "24.000000",
      remainingQuantityBase: "18.000000",
      reversedQuantityBase: "0.000000",
      skuCode: "JUICE-1L",
      skuId: "37989314-b1b7-4bea-9c68-b6390ddae80f",
      skuName: "Mango Juice 1L",
      trackingPolicy: "lot",
    },
  ],
  status: "partially_picked",
  version: 4,
  warehouseId: "dd2cabf2-3f01-4a5c-94a8-b1a580b1d0f4",
};

test("denies an inactive scan, requires reasoned manual FEFO fallback, and posts a partial pick", async ({
  page,
}) => {
  let postedBody: Record<string, unknown> | undefined;
  await page.route(`**/api/picking/${fulfillmentOrderId}`, async (route) => {
    if (route.request().method() === "POST") {
      postedBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        contentType: "application/json",
        json: {
          correlationId: "pick-posted",
          kind: "posted",
          pick: {
            fulfillmentOrderId,
            lines: [],
            pickId,
            pickedQuantityBase: "2.000000",
            remainingQuantityBase: "16.000000",
            status: "partially_picked",
            version: 5,
          },
        },
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      json: {
        context,
        correlationId: "pick-context",
        kind: "ready",
      },
    });
  });
  await page.route("**/api/picking/barcodes/resolve", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        code: "barcode_mapping_inactive",
        correlationId: "scan-denied",
        kind: "scan_denied",
        message: "The barcode mapping is inactive.",
      },
      status: 422,
    }),
  );
  await page.route(`**/api/picking/${fulfillmentOrderId}/picks`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "pick-list",
        items: [],
        kind: "ready",
        total: 0,
      },
    }),
  );

  await page.goto(`/picking?fulfillmentOrderId=${fulfillmentOrderId}`);
  await expect(
    page.getByRole("heading", { name: "Move proof, not just product." }),
  ).toBeVisible();
  await expect(page.getByText("Available", { exact: true })).toBeVisible();
  await expect(page.getByText("Identity stack", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Dispatch Staging", { exact: true }),
  ).toBeVisible();

  await page.getByLabel("Barcode scan").fill("INACTIVE-LOT");
  await page.getByRole("button", { name: "Resolve barcode" }).click();
  await expect(
    page.getByRole("heading", { name: "Scan denied" }),
  ).toBeVisible();
  await expect(page.getByText("barcode_mapping_inactive")).toBeVisible();

  await page
    .getByRole("button", { name: "Use authorized manual selection" })
    .click();
  await page.getByLabel("Lot code").fill("LOT-LATE");
  await page.getByLabel("Pick quantity").fill("2");
  await page
    .getByLabel("Manual selection reason")
    .fill("Scanner cannot read the damaged case label.");
  await page
    .getByLabel("FEFO override reason")
    .fill("Earlier lot is isolated for inspection.");
  await page.getByRole("button", { name: "Post partial pick" }).click();

  await expect(
    page.getByRole("heading", { name: "Partial pick posted" }),
  ).toBeVisible();
  await expect(page.getByText(/16\.000000 .* remains released/)).toBeVisible();
  expect(postedBody?.["idempotencyKey"]).toEqual(expect.any(String));
  const command = postedBody?.["command"] as {
    lines: Array<{ selections: Array<Record<string, unknown>> }>;
  };
  expect(command.lines[0]?.selections[0]).toEqual(
    expect.objectContaining({
      fefo_override_reason: "Earlier lot is isolated for inspection.",
      lot_code: "LOT-LATE",
      manual_reason: "Scanner cannot read the damaged case label.",
    }),
  );
});

test("distinguishes an empty queue from forbidden Warehouse work", async ({
  page,
}) => {
  await page.goto("/picking");
  await expect(
    page.getByRole("heading", { name: "The pick rail is clear" }),
  ).toBeVisible();

  await page.route(`**/api/picking/${fulfillmentOrderId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        code: "operational_scope_required",
        correlationId: "pick-forbidden",
        kind: "forbidden",
        message: "Warehouse Operational Scope is required for this Pick.",
      },
      status: 403,
    }),
  );
  await page.route(`**/api/picking/${fulfillmentOrderId}/picks`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "history-forbidden",
        items: [],
        kind: "ready",
        total: 0,
      },
    }),
  );
  await page.getByLabel("Fulfillment Order ID").fill(fulfillmentOrderId);
  await page.getByRole("button", { name: "Open pick work" }).click();
  await expect(
    page.getByRole("heading", { name: "Warehouse authority required" }),
  ).toBeVisible();
  await expect(page.getByText("operational_scope_required")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Retry unchanged work" }),
  ).toHaveCount(0);
});

test("shows unreleased Fulfillment work as blocked instead of an empty queue", async ({
  page,
}) => {
  await page.route(`**/api/picking/${fulfillmentOrderId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        context: { ...context, lines: [], status: "reserved" },
        correlationId: "pick-blocked",
        kind: "ready",
      },
    }),
  );
  await page.route(`**/api/picking/${fulfillmentOrderId}/picks`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "history-empty",
        items: [],
        kind: "ready",
        total: 0,
      },
    }),
  );
  await page.goto("/picking");
  await page.getByLabel("Fulfillment Order ID").fill(fulfillmentOrderId);
  await page.getByRole("button", { name: "Open pick work" }).click();
  await expect(
    page.getByRole("heading", { name: "Pick release is blocked" }),
  ).toBeVisible();
});

test("retains an uncertain command for retry and separates it from a server conflict", async ({
  page,
}) => {
  const commandKeys: string[] = [];
  let attempts = 0;
  await page.route(`**/api/picking/${fulfillmentOrderId}`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        json: {
          context,
          correlationId: "pick-context",
          kind: "ready",
        },
      });
      return;
    }
    const body = route.request().postDataJSON() as {
      idempotencyKey: string;
    };
    commandKeys.push(body.idempotencyKey);
    attempts += 1;
    await route.fulfill({
      contentType: "application/json",
      json:
        attempts === 1
          ? {
              code: "warehouse_service_unavailable",
              correlationId: "pick-uncertain",
              kind: "unavailable",
              message:
                "The Pick outcome is uncertain. Retry the unchanged command.",
            }
          : {
              code: "fulfillment_version_conflict",
              correlationId: "pick-conflict",
              kind: "conflict",
              message: "Refresh before posting the Pick.",
            },
      status: attempts === 1 ? 503 : 409,
    });
  });
  await page.route(`**/api/picking/${fulfillmentOrderId}/picks`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "pick-list",
        items: [],
        kind: "ready",
        total: 0,
      },
    }),
  );
  await page.route("**/api/picking/barcodes/resolve", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "barcode-resolved",
        kind: "resolved",
        resolution: {
          barcode: "LOT-EARLY-SCAN",
          barcodeMappingId: "barcode-map",
          baseQuantityPerUnit: "1.000000",
          expirationDate: "2026-08-20",
          lotCode: "LOT-EARLY",
          mappingType: "lot",
          serialNumber: null,
          skuCode: "JUICE-1L",
          skuId: context.lines[0]?.skuId,
          unitCode: "EA",
        },
      },
    }),
  );

  await page.goto(`/picking?fulfillmentOrderId=${fulfillmentOrderId}`);
  await page.getByLabel("Barcode scan").fill("LOT-EARLY-SCAN");
  await page.getByRole("button", { name: "Resolve barcode" }).click();
  await page.getByRole("button", { name: "Post partial pick" }).click();
  await expect(
    page.getByRole("heading", { name: "Safe retry retained" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry unchanged work" }).click();
  await expect(
    page.getByRole("heading", { name: "Authoritative pick changed" }),
  ).toBeVisible();
  expect(commandKeys).toHaveLength(2);
  expect(commandKeys[1]).toBe(commandKeys[0]);
});

test("shows completed custody and posts a linked reasoned reversal", async ({
  page,
}) => {
  const completedContext = {
    ...context,
    lines: context.lines.map((line) => ({
      ...line,
      pickedQuantityBase: "24.000000",
      remainingQuantityBase: "0.000000",
    })),
    status: "picked",
    version: 5,
  };
  let reversalBody: Record<string, unknown> | undefined;
  await page.route(`**/api/picking/${fulfillmentOrderId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        context: completedContext,
        correlationId: "pick-complete",
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
            actorSubject: "warehouse-clerk",
            correlationId: "pick-post",
            dispatched: false,
            eventType: "posted",
            lines: [],
            pickId,
            postedAt: "2026-07-29T12:00:00Z",
            quantityBase: "24.000000",
            reason: null,
            reversalOfPickId: null,
          },
        ],
        kind: "ready",
        total: 1,
      },
    }),
  );
  await page.route("**/api/picking/picks/*/reversal", async (route) => {
    reversalBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "pick-reversed",
        kind: "reversed",
        reversal: {
          fulfillmentOrderId,
          originalPickId: pickId,
          reversalPickId: "ebcdef1f-4712-478e-896b-67dc51058c0c",
          reversedQuantityBase: "24.000000",
          sourceMovementIds: ["available-in"],
          stagingMovementIds: ["staging-out"],
          status: "reversed",
          version: 6,
        },
      },
    });
  });

  await page.goto(`/picking?fulfillmentOrderId=${fulfillmentOrderId}`);
  await expect(
    page.getByRole("heading", { name: "Released quantity staged" }),
  ).toBeVisible();
  await page
    .getByLabel("Reversal reason")
    .fill("Tote damaged before dispatch.");
  await page.getByRole("button", { name: "Reverse staged pick" }).click();
  await expect(
    page.getByRole("heading", { name: "Reverse staged custody" }),
  ).toBeVisible();
  expect(reversalBody?.["idempotencyKey"]).toEqual(expect.any(String));
  expect((reversalBody?.["command"] as { reason: string }).reason).toBe(
    "Tote damaged before dispatch.",
  );
});
