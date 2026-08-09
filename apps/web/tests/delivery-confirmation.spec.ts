import { expect, test, type Page } from "@playwright/test";

const deliveryId = "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef";
const lineId = "4af0c99a-b55d-4f68-bf34-6f0805630032";

const assigned = {
  cacheTag: '"delivery-v1"',
  correlationId: "assigned-deliveries",
  items: [
    {
      assignedTo: "delivery-mnl",
      collectionAmountDue: null as string | null,
      collectionRequired: false,
      deliveryAddress: { city: "Manila" },
      deliveryId,
      evidenceRequirements: ["recipient_name", "signature"],
      fulfillmentOrderId: "765b5ab6-7f39-4671-8561-747755641016",
      lines: [
        {
          lineId,
          lotSelections: [],
          quantityBase: "2.000000",
          serialNumbers: [],
          skuCode: "JUICE-1L",
          skuId: "37989314-b1b7-4bea-9c68-b6390ddae80f",
          skuName: "Mango Juice 1L",
        },
      ],
      paymentTimingPolicy: "prepaid",
      recipientName: "Ana Santos",
      status: "dispatched",
      version: 1,
    },
  ],
  kind: "ready",
  total: 1,
};

async function openAssigned(page: Page, payload = assigned) {
  await page.route("**/api/deliveries", (route) =>
    route.fulfill({ contentType: "application/json", json: payload }),
  );
  await page.goto("/deliveries");
  await page.getByRole("button", { name: /Ana Santos/ }).click();
  await page.getByLabel("Signature evidence").setInputFiles({
    buffer: Buffer.from("signature-proof"),
    mimeType: "image/png",
    name: "signature.png",
  });
}

test("confirms COD cash and proof as one stable command", async ({ page }) => {
  await openAssigned(page, {
    ...assigned,
    items: [
      {
        ...assigned.items[0]!,
        collectionAmountDue: "224.00",
        collectionRequired: true,
        paymentTimingPolicy: "cash_on_delivery",
      },
    ],
  });
  let confirmationCommand: Record<string, unknown> | null = null;
  await page.route(
    `**/api/deliveries/${deliveryId}/confirmation`,
    async (route) => {
      const body = route.request().postDataJSON() as {
        action: string;
        command?: Record<string, unknown>;
      };
      if (body.action === "intent" || body.action === "complete") {
        await route.fulfill({
          contentType: "application/json",
          json: {
            evidence_id: "cod-proof",
            expires_at: null,
            parts: [],
            status: "verified",
            upload_id: null,
          },
        });
        return;
      }
      confirmationCommand = body.command ?? null;
      await route.fulfill({
        contentType: "application/json",
        json: {
          collection: {
            amount_collected: "224.00",
            cash_reconciliation_status: "pending",
            payment_method: "cash",
            status: "cleared",
          },
          confirmation_id: "cod-confirmation",
          delivery_id: deliveryId,
          delivery_receipt: {
            delivery_receipt_id: "cod-receipt",
            number: "DR-MNL-00000001",
            status: "pending_document",
          },
          lines: [],
          outbox_event_id: "cod-event",
          status: "confirmed",
          version: 2,
        },
      });
    },
  );
  await page
    .getByRole("button", { name: "Confirm COD collection and delivery" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Delivery confirmed" }),
  ).toBeVisible();
  await expect(
    page.getByText(/COD cash collection of PHP 224.00 cleared/),
  ).toBeVisible();
  expect(confirmationCommand).toMatchObject({
    collection: {
      amount: "224.00",
      currency: "PHP",
      payment_method: "cash",
    },
  });
});

test("links cleared non-cash COD and rejects malformed money locally", async ({
  page,
}) => {
  await openAssigned(page, {
    ...assigned,
    items: [
      {
        ...assigned.items[0]!,
        collectionAmountDue: "224.00",
        collectionRequired: true,
        paymentTimingPolicy: "cash_on_delivery",
      },
    ],
  });
  await page.getByLabel("COD amount collected").fill(".");
  await expect(
    page.getByRole("button", { name: "Confirm COD collection and delivery" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "noncash" }).click();
  await page.getByLabel("COD amount collected").fill("224.00");
  await page
    .getByLabel("Cleared Payment Receipt ID")
    .fill("7bf0d080-e08d-4bac-8375-0a6c2c914029");
  let command: Record<string, unknown> | undefined;
  await page.route(`**/api/deliveries/${deliveryId}/confirmation`, (route) => {
    const body = route.request().postDataJSON() as {
      action: string;
      command?: Record<string, unknown>;
    };
    if (body.action === "confirm") command = body.command;
    return route.fulfill({
      contentType: "application/json",
      json:
        body.action === "confirm"
          ? {
              collection: {
                amount_collected: "224.00",
                cash_reconciliation_status: null,
                payment_method: "bank_transfer",
                status: "cleared",
              },
              confirmation_id: "cod-confirmation",
              delivery_id: deliveryId,
              delivery_receipt: {
                delivery_receipt_id: "cod-receipt",
                number: "DR-MNL-00000001",
                status: "pending_document",
              },
              lines: [],
              outbox_event_id: "cod-event",
              status: "confirmed",
              version: 2,
            }
          : {
              evidence_id: "cod-proof",
              expires_at: null,
              parts: [],
              status: "verified",
              upload_id: null,
            },
    });
  });
  await page
    .getByRole("button", { name: "Confirm COD collection and delivery" })
    .click();
  await expect.poll(() => command).toBeDefined();
  expect(command).toMatchObject({
    collection: {
      payment_method: "bank_transfer",
      payment_receipt_id: "7bf0d080-e08d-4bac-8375-0a6c2c914029",
    },
  });
});

test("confirms COD using an approved On Account conversion", async ({
  page,
}) => {
  await openAssigned(page, {
    ...assigned,
    items: [
      {
        ...assigned.items[0]!,
        collectionAmountDue: "224.00",
        collectionRequired: true,
        paymentTimingPolicy: "cash_on_delivery",
      },
    ],
  });
  await page.getByRole("button", { name: "on account" }).click();
  await page
    .getByLabel("Approved On Account conversion ID")
    .fill("a704d621-df0d-487c-8b84-e822d049b411");
  let command: Record<string, unknown> | undefined;
  await page.route(`**/api/deliveries/${deliveryId}/confirmation`, (route) => {
    const body = route.request().postDataJSON() as {
      action: string;
      command?: Record<string, unknown>;
    };
    if (body.action === "confirm") command = body.command;
    return route.fulfill({
      contentType: "application/json",
      json:
        body.action === "confirm"
          ? {
              confirmation_id: "cod-confirmation",
              delivery_id: deliveryId,
              delivery_receipt: {
                delivery_receipt_id: "cod-receipt",
                number: "DR-MNL-00000001",
                status: "pending_document",
              },
              lines: [],
              on_account_conversion: {
                amount: "224.00",
                status: "consumed",
              },
              outbox_event_id: "cod-event",
              status: "confirmed",
              version: 2,
            }
          : {
              evidence_id: "cod-proof",
              expires_at: null,
              parts: [],
              status: "verified",
              upload_id: null,
            },
    });
  });
  await page
    .getByRole("button", { name: "Confirm COD collection and delivery" })
    .click();
  await expect.poll(() => command).toBeDefined();
  expect(command).toMatchObject({
    on_account_conversion_id: "a704d621-df0d-487c-8b84-e822d049b411",
  });
  expect(command).not.toHaveProperty("collection");
});

test("renders empty assigned and captured confirmation states", async ({
  page,
}) => {
  await page.route("**/api/deliveries", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { ...assigned, items: [], total: 0 },
    }),
  );
  await page.goto("/deliveries");
  await expect(
    page.getByRole("heading", { name: "No assigned Deliveries" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "No captured confirmations" }),
  ).toBeVisible();
});

test("uploads signed proof and renders pending, confirmed, and unavailable receipt states", async ({
  page,
}) => {
  await openAssigned(page);
  await page.route(
    `**/api/deliveries/${deliveryId}/confirmation`,
    async (route) => {
      const body = route.request().postDataJSON() as { action: string };
      if (body.action === "intent") {
        await route.fulfill({
          contentType: "application/json",
          json: {
            evidence_id: "proof",
            expires_at: "2026-08-01T14:00:00Z",
            part_size: 5242880,
            parts: [
              {
                end_byte: 15,
                part_number: 1,
                start_byte: 0,
                upload_headers: { "Content-Type": "image/png" },
                upload_url: "http://127.0.0.1:3000/signed-evidence",
              },
            ],
            status: "uploading",
            upload_id: "multipart-proof",
          },
          status: 201,
        });
        return;
      }
      if (body.action === "complete") {
        await route.fulfill({
          contentType: "application/json",
          json: {
            evidence_id: "proof",
            expires_at: null,
            status: "verified",
            upload_headers: {},
            upload_url: null,
          },
        });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
      await route.fulfill({
        contentType: "application/json",
        json: {
          confirmation_id: "65a4745a-7d07-4cc2-a497-bc27f60be7a0",
          delivery_id: deliveryId,
          delivery_receipt: {
            delivery_receipt_id: "d98873ae-7cf1-48b6-b2e5-129d23bd9f81",
            number: "DR-MNL-00000001",
            status: "pending_document",
          },
          lines: [],
          outbox_event_id: "af9cf881-e5af-48b0-95e8-04534241b330",
          status: "confirmed",
          version: 2,
        },
      });
    },
  );
  await page.route("**/signed-evidence", (route) =>
    route.fulfill({ status: 200 }),
  );
  await page.route("**/api/delivery-receipts/**", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        delivery_id: deliveryId,
        delivery_receipt_id: "d98873ae-7cf1-48b6-b2e5-129d23bd9f81",
        number: "DR-MNL-00000001",
        snapshot: {},
        status: "unavailable",
      },
    }),
  );
  await page.getByRole("button", { name: "Confirm accepted quantity" }).click();
  await expect(
    page.getByRole("heading", { name: "Pending Sync" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Delivery confirmed" }),
  ).toBeVisible();
  await expect(
    page.getByText("Receipt unavailable — rendering in progress."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Refresh receipt" }).click();
  await expect(
    page.getByText(
      "Receipt rendering unavailable — background retry scheduled.",
    ),
  ).toBeVisible();
});

test("retries signed upload with stable evidence and command identities", async ({
  page,
}) => {
  await openAssigned(page);
  const evidenceIds: string[] = [];
  const confirmationIds: string[] = [];
  await page.route(
    `**/api/deliveries/${deliveryId}/confirmation`,
    async (route) => {
      const body = route.request().postDataJSON() as {
        action: string;
        command?: { confirmation_id?: string; evidence_id?: string };
      };
      if (body.action === "intent") {
        evidenceIds.push(body.command?.evidence_id ?? "missing");
        await route.fulfill({
          contentType: "application/json",
          json: {
            evidence_id: body.command?.evidence_id,
            expires_at: "2026-08-01T14:00:00Z",
            part_size: 5242880,
            parts: [
              {
                end_byte: 15,
                part_number: 1,
                start_byte: 0,
                upload_headers: {},
                upload_url: "http://127.0.0.1:3000/signed-evidence",
              },
            ],
            status: "uploading",
            upload_id: "multipart-proof",
          },
          status: 201,
        });
        return;
      }
      if (body.action === "complete") {
        await route.fulfill({
          contentType: "application/json",
          json: {
            evidence_id: evidenceIds[0],
            expires_at: null,
            part_size: null,
            parts: [],
            status: "verified",
            upload_id: null,
          },
        });
        return;
      }
      confirmationIds.push(body.command?.confirmation_id ?? "missing");
      await route.fulfill({
        contentType: "application/json",
        json: {
          confirmation_id: body.command?.confirmation_id,
          delivery_id: deliveryId,
          delivery_receipt: {
            delivery_receipt_id: "d98873ae-7cf1-48b6-b2e5-129d23bd9f81",
            number: "DR-MNL-00000001",
            status: "pending_document",
          },
          lines: [],
          outbox_event_id: "af9cf881-e5af-48b0-95e8-04534241b330",
          status: "confirmed",
          version: 2,
        },
      });
    },
  );
  let uploadAttempt = 0;
  await page.route("**/signed-evidence", (route) => {
    uploadAttempt += 1;
    return route.fulfill({ status: uploadAttempt === 1 ? 503 : 200 });
  });
  await page.getByRole("button", { name: "Confirm accepted quantity" }).click();
  await expect(
    page.getByRole("heading", { name: "Upload failed — evidence retained" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Confirm accepted quantity" }).click();
  await expect(
    page.getByRole("heading", { name: "Delivery confirmed" }),
  ).toBeVisible();
  expect(evidenceIds).toHaveLength(2);
  expect(new Set(evidenceIds).size).toBe(1);
  expect(confirmationIds).toHaveLength(1);
});

for (const scenario of [
  { kind: "forbidden", status: 403, title: "Confirmation forbidden" },
  {
    kind: "conflict",
    status: 409,
    title: "Confirmation conflict — review required",
  },
] as const) {
  test(`renders ${scenario.kind} acknowledgement for explicit review`, async ({
    page,
  }) => {
    await openAssigned(page);
    await page.route(
      `**/api/deliveries/${deliveryId}/confirmation`,
      async (route) => {
        const body = route.request().postDataJSON() as { action: string };
        if (body.action === "intent" || body.action === "complete") {
          await route.fulfill({
            contentType: "application/json",
            json: {
              evidence_id: "proof",
              expires_at: null,
              status: "verified",
              upload_headers: {},
              upload_url: null,
            },
          });
          return;
        }
        await route.fulfill({
          contentType: "application/json",
          json: {
            code: `delivery_${scenario.kind}`,
            correlationId: `${scenario.kind}-correlation`,
            kind: scenario.kind,
            message: "The authoritative Delivery changed.",
          },
          status: scenario.status,
        });
      },
    );
    await page
      .getByRole("button", { name: "Confirm accepted quantity" })
      .click();
    await expect(
      page.getByRole("heading", { name: scenario.title }),
    ).toBeVisible();
    await expect(
      page.getByText(new RegExp(`${scenario.kind}-correlation`)),
    ).toBeVisible();
  });
}
