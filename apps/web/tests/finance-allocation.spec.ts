import { expect, test } from "@playwright/test";

const branchId = "efad4205-5060-49fb-b752-3faca649ca6e";
const customerId = "98481a1c-e493-41a6-851b-93142553ceab";
const receiptId = "d2528c7a-c76a-42b1-a427-cde44d61f0b4";
const invoiceId = "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef";

function clearedReceipt(state: {
  allocatedAmount?: string;
  applicationState: string;
  unappliedAmount: string;
}) {
  return {
    allocated_amount: state.allocatedAmount ?? "0.00",
    amount: "500.00",
    application_state: state.applicationState,
    available_for_coverage: state.unappliedAmount,
    balance_version: 1,
    branch_id: branchId,
    cash_reconciliation_status: null,
    cleared_amount: "500.00",
    currency: "PHP",
    customer_id: customerId,
    external_reference: null,
    external_reference_normalized: null,
    payment_method: "bank_transfer",
    payment_receipt_id: receiptId,
    received_at: "2026-07-29T02:00:00Z",
    recorded_by: "finance-recorder",
    reversal_id: null,
    sales_order_id: null,
    status: "cleared",
    unapplied_amount: state.unappliedAmount,
    verified_by: "finance-verifier",
  };
}

function allocationDetail(state: {
  allocatedAmount: string;
  applicationState: string;
  availableAmount: string;
  allocations: Array<{
    allocation_id: string;
    amount: string;
    invoice_id: string;
  }>;
}) {
  return {
    allocated_amount: state.allocatedAmount,
    allocations: state.allocations,
    application_state: state.applicationState,
    available_amount: state.availableAmount,
    cleared_amount: "500.00",
    coverage_designated_amount: "0.00",
    payment_receipt_id: receiptId,
    version: 1,
  };
}

function openInvoice(openBalance: string) {
  return {
    items: [
      {
        branch_id: branchId,
        created_at: "2026-07-28T02:00:00Z",
        currency: "PHP",
        customer_id: customerId,
        delivery_confirmation_id: null,
        discount_total: "0.00",
        draft_invoice_id: invoiceId,
        grand_total: "300.00",
        invoice_kind: "sales",
        line_total: "300.00",
        lines: [],
        open_balance: openBalance,
        posted_at: "2026-07-28T02:00:00Z",
        sales_order_id: null,
        sales_order_revision_id: null,
        status: "posted",
        subtotal: "300.00",
        tax_total: "0.00",
      },
    ],
    total: 1,
  };
}

test("allocates a cleared receipt to an open invoice and retains excess", async ({
  page,
}) => {
  let listState = "unapplied";
  let allocatedAmount = "0.00";
  let availableAmount = "500.00";
  let allocations: Array<{
    allocation_id: string;
    amount: string;
    invoice_id: string;
  }> = [];

  await page.route("/api/finance/payment-receipts", async (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        items: [
          clearedReceipt({
            applicationState: listState,
            unappliedAmount: availableAmount,
          }),
        ],
        total: 1,
      },
    }),
  );
  await page.route(
    `/api/finance/payment-receipts/${receiptId}/allocations`,
    async (route) => {
      if (route.request().method() === "POST") {
        allocatedAmount = "300.00";
        availableAmount = "200.00";
        listState = "partially_applied";
        allocations = [
          {
            allocation_id: "a1111111-1111-1111-1111-111111111111",
            amount: "300.00",
            invoice_id: invoiceId,
          },
        ];
        return route.fulfill({
          contentType: "application/json",
          json: allocations,
          status: 201,
        });
      }
      return route.fulfill({
        contentType: "application/json",
        json: allocationDetail({
          allocatedAmount,
          applicationState: listState,
          availableAmount,
          allocations,
        }),
      });
    },
  );
  await page.route(
    `/api/finance/invoices?customer_id=${customerId}&open_only=true&status=posted`,
    async (route) =>
      route.fulfill({
        contentType: "application/json",
        json: openInvoice("300.00"),
      }),
  );

  await page.goto("/finance/allocations");
  await expect(
    page.getByRole("heading", { level: 1, name: "Payment allocation" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Select" }).click();

  await expect(
    page.getByText("unapplied · PHP 500.00 unapplied"),
  ).toBeVisible();
  await page.getByLabel("Open invoice").selectOption(invoiceId);
  await page.getByLabel("Amount to allocate").fill("300.00");
  await page.getByRole("button", { name: "Allocate to invoice" }).click();

  await expect(
    page.getByText(`Allocated PHP 300.00 to invoice ${invoiceId}`),
  ).toBeVisible();
  await expect(
    page.getByText("Excess customer funds remain Unapplied Payment"),
  ).toBeVisible();
  await expect(
    page.getByText("partially applied · PHP 200.00 unapplied"),
  ).toBeVisible();
});

test("rejects stale balance version and preserves retry identity", async ({
  page,
}) => {
  const requests: Array<{ idempotencyKey: string | null; status: number }> = [];
  let attempt = 0;

  await page.route("/api/finance/payment-receipts", async (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        items: [
          clearedReceipt({
            applicationState: "unapplied",
            unappliedAmount: "500.00",
          }),
        ],
        total: 1,
      },
    }),
  );
  await page.route(
    `/api/finance/payment-receipts/${receiptId}/allocations`,
    async (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          contentType: "application/json",
          json: allocationDetail({
            allocatedAmount: "0.00",
            applicationState: "unapplied",
            availableAmount: "500.00",
            allocations: [],
          }),
        });
      }
      attempt += 1;
      const body = route.request().postDataJSON() as {
        idempotencyKey?: string;
      } | null;
      const idempotencyKey = body?.idempotencyKey ?? null;
      if (attempt === 1) {
        requests.push({ idempotencyKey, status: 409 });
        return route.fulfill({
          contentType: "application/json",
          json: {
            code: "payment_balance_version_conflict",
            correlation_id: "version-conflict",
            message:
              "The Payment Receipt balance changed and requires refresh.",
          },
          status: 409,
        });
      }
      requests.push({ idempotencyKey, status: 201 });
      return route.fulfill({
        contentType: "application/json",
        json: [
          {
            allocation_id: "a2222222-2222-2222-2222-222222222222",
            amount: "300.00",
            invoice_id: invoiceId,
          },
        ],
        status: 201,
      });
    },
  );
  await page.route(
    `/api/finance/invoices?customer_id=${customerId}&open_only=true&status=posted`,
    async (route) =>
      route.fulfill({
        contentType: "application/json",
        json: openInvoice("300.00"),
      }),
  );

  await page.goto("/finance/allocations");
  await page.getByRole("button", { name: "Select" }).click();
  await page.getByLabel("Open invoice").selectOption(invoiceId);
  await page.getByLabel("Amount to allocate").fill("300.00");
  await page.getByRole("button", { name: "Allocate to invoice" }).click();

  await expect(page.getByText("Allocation was not accepted")).toBeVisible();
  await page.getByRole("button", { name: "Allocate to invoice" }).click();
  await expect(page.getByText("Allocated PHP 300.00 to invoice")).toBeVisible();

  expect(requests).toHaveLength(2);
  expect(requests[0]!.idempotencyKey).toBeTruthy();
  expect(requests[0]!.idempotencyKey).toBe(requests[1]!.idempotencyKey);
});

test("discloses unapplied payments separately on the customer statement", async ({
  page,
}) => {
  await page.route(
    `/api/finance/customers/${customerId}/statement**`,
    async (route) =>
      route.fulfill({
        contentType: "application/json",
        json: {
          as_of: "2026-07-29",
          closing_balance: "1000.00",
          currency: "PHP",
          customer_id: customerId,
          documents: [],
          from_date: "2026-06-29",
          lines: [],
          opening_balance: "1000.00",
          to_date: "2026-07-29",
          unapplied_payment_total: "200.00",
          unapplied_payments: [
            {
              allocated_amount: "300.00",
              amount: "500.00",
              application_state: "partially_applied",
              payment_method: "bank_transfer",
              payment_receipt_id: receiptId,
              received_at: "2026-07-29",
              unapplied_amount: "200.00",
            },
          ],
        },
      }),
  );

  await page.goto("/finance/statement");
  await page.getByLabel("Customer ID").fill(customerId);
  await page.getByRole("button", { name: "Run statement" }).click();

  await expect(page.getByText("200.00").first()).toBeVisible();
  await expect(page.getByText("partially applied")).toBeVisible();
  await expect(
    page.getByText("Receipt 500.00 · allocated 300.00"),
  ).toBeVisible();
});

test.use({ viewport: { width: 390, height: 844 } });
test("retains excess on a mobile-web viewport", async ({ page }) => {
  await page.route("/api/finance/payment-receipts", async (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        items: [
          clearedReceipt({
            applicationState: "unapplied",
            unappliedAmount: "500.00",
          }),
        ],
        total: 1,
      },
    }),
  );
  await page.route(
    `/api/finance/payment-receipts/${receiptId}/allocations`,
    async (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          contentType: "application/json",
          json: [
            {
              allocation_id: "a3333333-3333-3333-3333-333333333333",
              amount: "250.00",
              invoice_id: invoiceId,
            },
          ],
          status: 201,
        });
      }
      return route.fulfill({
        contentType: "application/json",
        json: allocationDetail({
          allocatedAmount: "0.00",
          applicationState: "unapplied",
          availableAmount: "500.00",
          allocations: [],
        }),
      });
    },
  );
  await page.route(
    `/api/finance/invoices?customer_id=${customerId}&open_only=true&status=posted`,
    async (route) =>
      route.fulfill({
        contentType: "application/json",
        json: openInvoice("300.00"),
      }),
  );

  await page.goto("/finance/allocations");
  await page.getByRole("button", { name: "Select" }).click();
  await page.getByLabel("Open invoice").selectOption(invoiceId);
  await page.getByLabel("Amount to allocate").fill("250.00");
  await page.getByRole("button", { name: "Allocate to invoice" }).click();

  await expect(page.getByText("Allocated PHP 250.00 to invoice")).toBeVisible();
});
