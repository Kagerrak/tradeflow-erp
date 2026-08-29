import { expect, test } from "@playwright/test";

const requestId = "a341427a-9442-4c31-8591-230160028a2a";
const authorized = {
  affected_value_base_currency: "112.000000",
  authorized_at: "2026-08-13T09:00:00Z",
  authorized_by: "checker-1",
  base_currency: "PHP",
  branch_id: "branch-mnl",
  confirmation_id: "confirmation-1",
  delivery_id: "delivery-1",
  delivery_receipt_id: "receipt-1",
  lines: [
    {
      delivered_quantity_base: "2.000000",
      delivery_line_id: "delivery-line-1",
      eligible_quantity_base: "2.000000",
      line_id: "line-1",
      quantity_base: "1.000000",
      return_request_line_id: "return-line-1",
      sku_id: "SKU-DEFECT-1",
    },
  ],
  notes: "Sealed-unit defect",
  reason_code: "PRODUCT_DEFECT",
  reason_label: "Product defect",
  requested_at: "2026-08-13T08:00:00Z",
  requested_by: "maker-1",
  responsible_party_code: "SUPPLIER",
  responsible_party_label: "Supplier",
  return_request_id: requestId,
  status: "authorized",
  version: 2,
  warehouse_id: "warehouse-mnl",
};

const verifiedEvidence = {
  evidence_id: "evidence-1",
  expires_at: null,
  part_size: null,
  parts: [],
  status: "verified",
  upload_id: null,
};

const receiptResponse = {
  lines: [
    {
      custody: "available",
      delivery_line_id: "delivery-line-1",
      line_id: "line-1",
      movement_id: "movement-1",
      outcome: "restock",
      received_quantity_base: "1.000000",
      return_request_line_id: "line-1",
      sku_id: "SKU-DEFECT-1",
    },
  ],
  notes: "Resealed.",
  received_at: "2026-08-13T10:00:00Z",
  received_by: "receiver-1",
  return_receipt_id: "receipt-uuid-1",
  return_request_id: requestId,
  status: "received",
  version: 3,
};

test("posts a Return Receipt and retries idempotently on 503", async ({
  page,
}) => {
  await page.route("**/api/return-requests?status=authorized", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [authorized], total: 1 },
    }),
  );
  await page.route(
    `**/api/return-requests/${requestId}/evidence/uploads`,
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: verifiedEvidence,
      }),
  );
  await page.route(
    `**/api/return-requests/${requestId}/evidence/*/complete`,
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: verifiedEvidence,
      }),
  );
  const commands: unknown[] = [];
  await page.route(`**/api/return-requests/${requestId}/receipts`, (route) => {
    commands.push(route.request().postDataJSON());
    if (commands.length === 1) {
      return route.fulfill({
        contentType: "application/json",
        json: { message: "The outcome is uncertain." },
        status: 503,
      });
    }
    return route.fulfill({
      contentType: "application/json",
      json: receiptResponse,
    });
  });

  await page.goto("/returns");
  await page.getByRole("button", { name: "Receipt / Inspection" }).click();
  await expect(
    page.getByRole("heading", { name: "Return receipts" }),
  ).toBeVisible();
  await page.getByRole("button", { name: new RegExp(requestId) }).click();
  await expect(page.getByText("Product defect (PRODUCT_DEFECT)")).toBeVisible();
  await page.getByLabel("Received quantity").fill("1.000000");
  await page.getByLabel("Inspection photos").setInputFiles({
    buffer: Buffer.from("fake-image"),
    mimeType: "image/png",
    name: "photo.png",
  });
  await page.getByRole("button", { name: "Post return receipt" }).click();
  await expect(page.getByText("The outcome is uncertain.")).toBeVisible();
  await page.getByRole("button", { name: "Post return receipt" }).click();
  await expect(page.getByText("Return Request a341427a")).toBeVisible();
  await expect(page.getByText("is now received")).toBeVisible();
  expect(commands[0]).toEqual(commands[1]);
  expect(commands[1]).toMatchObject({
    command: {
      expected_request_version: 2,
      lines: [
        {
          outcome: "restock",
          received_quantity_base: "1.000000",
          return_request_line_id: "return-line-1",
        },
      ],
    },
  });
});

test("clears received quantity when a line is rejected", async ({ page }) => {
  await page.route("**/api/return-requests?status=authorized", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { items: [authorized], total: 1 },
    }),
  );
  await page.route(
    `**/api/return-requests/${requestId}/evidence/uploads`,
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: verifiedEvidence,
      }),
  );
  await page.route(
    `**/api/return-requests/${requestId}/evidence/*/complete`,
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: verifiedEvidence,
      }),
  );
  await page.route(`**/api/return-requests/${requestId}/receipts`, (route) =>
    route.fulfill({ contentType: "application/json", json: receiptResponse }),
  );

  await page.goto("/returns");
  await page.getByRole("button", { name: "Receipt / Inspection" }).click();
  await page.getByRole("button", { name: new RegExp(requestId) }).click();
  await page.getByLabel("Outcome").selectOption("rejected");
  await expect(page.getByLabel("Received quantity")).toHaveValue("0");
  await page.getByLabel("Inspection photos").setInputFiles({
    buffer: Buffer.from("fake-image"),
    mimeType: "image/png",
    name: "photo.png",
  });
  await page.getByRole("button", { name: "Post return receipt" }).click();
  await expect(page.getByText("Return Request a341427a")).toBeVisible();
});
