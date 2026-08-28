import { expect, test } from "@playwright/test";

const requestId = "a341427a-9442-4c31-8591-230160028a2a";
const pending = {
  affected_value_base_currency: "112.000000",
  authorized_at: null,
  authorized_by: null,
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
  status: "pending_authorization",
  version: 1,
  warehouse_id: "warehouse-mnl",
};

const classifications = {
  reasons: [
    { code: "PRODUCT_DEFECT", label: "Product defect" },
    { code: "WRONG_ITEM", label: "Wrong item" },
  ],
  responsible_parties: [
    { code: "CUSTOMER", label: "Customer" },
    { code: "SUPPLIER", label: "Supplier" },
  ],
};

const eligibility = {
  delivery_receipt_id: "receipt-1",
  number: "DR-000001",
  lines: [
    {
      delivered_quantity_base: "2.000000",
      delivery_line_id: "delivery-line-1",
      eligible_quantity_base: "2.000000",
      line_id: "line-1",
      sku_id: "SKU-DEFECT-1",
    },
    {
      delivered_quantity_base: "3.000000",
      delivery_line_id: "delivery-line-2",
      eligible_quantity_base: "1.500000",
      line_id: "line-2",
      sku_id: "SKU-DEFECT-2",
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/return-classifications", (route) =>
    route.fulfill({ contentType: "application/json", json: classifications }),
  );
});

test("reviews and authorizes a Return Request against remaining quantity", async ({
  page,
}) => {
  await page.route(
    "**/api/return-requests?status=pending_authorization",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: { items: [pending], total: 1 },
      }),
  );
  const commands: unknown[] = [];
  await page.route(
    `**/api/return-requests/${requestId}/authorization`,
    (route) => {
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
        json: {
          ...pending,
          authorized_at: "2026-08-13T09:00:00Z",
          authorized_by: "checker-1",
          status: "authorized",
          version: 2,
        },
      });
    },
  );

  await page.goto("/returns");
  await expect(
    page.getByRole("heading", { name: "Return authorizations" }),
  ).toBeVisible();
  await page.getByRole("button", { name: new RegExp(requestId) }).click();
  await expect(page.getByLabel("Return eligibility")).toContainText(
    "1.000000 of 2.000000",
  );
  await expect(page.getByLabel("Return classification")).toContainText(
    "Product defect",
  );
  await page
    .getByRole("checkbox", {
      name: "I reviewed delivery eligibility and responsibility",
    })
    .check();
  await page.getByRole("button", { name: "Authorize return" }).click();
  await expect(page.getByText("The outcome is uncertain.")).toBeVisible();
  await page.getByRole("button", { name: "Authorize return" }).click();
  await expect(page.getByText("Authorized by checker-1")).toBeVisible();
  expect(commands[0]).toEqual(commands[1]);
  expect(commands[1]).toMatchObject({
    command: { expected_request_version: 1 },
  });
});

test("lists evidence and adds a note", async ({ page }) => {
  const evidenceId = "evidence-note-1";
  await page.route(
    "**/api/return-requests?status=pending_authorization",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: { items: [pending], total: 1 },
      }),
  );
  await page.route(`**/api/return-requests/${requestId}/evidence`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        items: [
          {
            captured_by: "maker-1",
            content_type: null,
            created_at: "2026-08-13T08:30:00Z",
            device_captured_at: "2026-08-13T08:30:00Z",
            evidence_id: evidenceId,
            kind: "note",
            note_text: "Initial note",
            sha256: null,
            size_bytes: null,
            status: "verified",
            verified_at: "2026-08-13T08:30:00Z",
          },
        ],
      },
    }),
  );
  await page.route(`**/api/return-requests/${requestId}/sync-state`, (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        acknowledged_at: "2026-08-13T08:30:00Z",
        conflict_detected_at: null,
        conflict_reason: null,
        current_version: 1,
        expected_version: 1,
        return_request_id: requestId,
        status: "acknowledged",
      },
    }),
  );
  const notes: unknown[] = [];
  await page.route(
    `**/api/return-requests/${requestId}/evidence/notes`,
    (route) => {
      notes.push(route.request().postDataJSON());
      return route.fulfill({
        contentType: "application/json",
        json: {
          captured_by: "checker-1",
          content_type: null,
          created_at: "2026-08-13T09:00:00Z",
          device_captured_at: "2026-08-13T09:00:00Z",
          evidence_id: "evidence-note-2",
          kind: "note",
          note_text: (
            notes[notes.length - 1] as { command: { note_text: string } }
          ).command.note_text,
          sha256: null,
          size_bytes: null,
          status: "verified",
          verified_at: "2026-08-13T09:00:00Z",
        },
      });
    },
  );

  await page.goto("/returns");
  await page.getByRole("button", { name: new RegExp(requestId) }).click();
  await expect(page.getByLabel("Return evidence")).toContainText(
    "Initial note",
  );
  await expect(page.getByLabel("Return evidence")).toContainText(
    "Sync: acknowledged",
  );
  await page.getByLabel("Add note").fill("Follow-up note");
  await page.getByRole("button", { name: "Save note" }).click();
  await expect(page.getByLabel("Return evidence")).toContainText(
    "Follow-up note",
  );
  expect(notes[0]).toMatchObject({
    command: { note_text: "Follow-up note" },
  });
});

test("creates a Return Request from a Delivery Receipt", async ({ page }) => {
  await page.route(
    "**/api/return-requests?status=pending_authorization",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        json: { items: [], total: 0 },
      }),
  );
  const submissions: Record<string, unknown>[] = [];
  await page.route("**/api/return-requests", (route) => {
    submissions.push(route.request().postDataJSON() as Record<string, unknown>);
    if (submissions.length === 1) {
      return route.fulfill({
        contentType: "application/json",
        json: { message: "The outcome is uncertain." },
        status: 503,
      });
    }
    return route.fulfill({ contentType: "application/json", json: pending });
  });
  await page.route(
    "**/api/delivery-receipts/receipt-1/return-eligibility",
    (route) =>
      route.fulfill({ contentType: "application/json", json: eligibility }),
  );
  await page.goto("/returns");
  await page
    .getByLabel("Delivery Receipt ID (paste or scan)")
    .fill("receipt-1");
  await page.getByRole("button", { name: "Load delivered lines" }).click();
  await expect(page.getByText("Receipt DR-000001")).toBeVisible();
  await page.getByLabel(/SKU SKU-DEFECT-1/).check();
  await page.getByLabel(/SKU SKU-DEFECT-2/).check();
  await page.getByLabel("Return quantity for SKU-DEFECT-1").fill("1.000000");
  await page.getByLabel("Return quantity for SKU-DEFECT-2").fill("0.500000");
  await page.getByLabel("Return reason").selectOption("WRONG_ITEM");
  await page.getByLabel("Responsible party").selectOption("CUSTOMER");
  await page.getByRole("button", { name: "Create return request" }).click();
  await expect(page.getByText("The outcome is uncertain.")).toBeVisible();
  await page.getByRole("button", { name: "Create return request" }).click();
  await expect(page.getByRole("heading", { name: requestId })).toBeVisible();
  expect(submissions[0]).toEqual(submissions[1]);
  expect(submissions[1]).toMatchObject({
    command: {
      lines: [
        { delivery_line_id: "delivery-line-1", quantity_base: "1.000000" },
        { delivery_line_id: "delivery-line-2", quantity_base: "0.500000" },
      ],
      reason_code: "WRONG_ITEM",
      reason_label: "Wrong item",
      responsible_party_code: "CUSTOMER",
      responsible_party_label: "Customer",
    },
    receiptId: "receipt-1",
  });
});
