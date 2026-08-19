import { expect, test } from "@playwright/test";

const pendingReceipt = {
  allocatedAmount: "0.00",
  amount: "224.00",
  applicationState: "not_cleared",
  availableForCoverage: "0.00",
  balanceVersion: 1,
  branchId: "efad4205-5060-49fb-b752-3faca649ca6e",
  cashReconciliationStatus: null,
  clearedAmount: "0.00",
  currency: "PHP",
  customerId: "98481a1c-e493-41a6-851b-93142553ceab",
  externalReference: "BANK-123",
  externalReferenceNormalized: "BANK-123",
  paymentMethod: "bank_transfer",
  paymentReceiptId: "d2528c7a-c76a-42b1-a427-cde44d61f0b4",
  receivedAt: "2026-07-29T02:00:00Z",
  recordedBy: "finance-recorder",
  reversalId: null,
  salesOrderId: "99596045-e62d-46b4-8521-739f0bde2359",
  status: "pending_verification",
  unappliedAmount: "0.00",
  verifiedBy: null,
};

test("records cash and gives the checker a method-specific next action", async ({
  page,
}) => {
  let recordedBody: Record<string, unknown> | undefined;
  let verificationBody: Record<string, unknown> | undefined;
  let conversionBody: Record<string, unknown> | undefined;
  let reconciliationBody: Record<string, unknown> | undefined;
  await page.route("**/api/payments?status=pending_verification", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "payment-queue",
        items: [pendingReceipt],
        kind: "ready",
        total: 1,
      },
    }),
  );
  await page.route("**/api/payments", async (route) => {
    recordedBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "payment-recorded",
        kind: "recorded",
        receipt: {
          ...pendingReceipt,
          allocatedAmount: "0.00",
          applicationState: "unapplied",
          availableForCoverage: "224.00",
          balanceVersion: 2,
          cashReconciliationStatus: "unreconciled",
          clearedAmount: "224.00",
          externalReference: null,
          externalReferenceNormalized: null,
          paymentMethod: "cash",
          status: "cleared",
          unappliedAmount: "224.00",
        },
      },
    });
  });
  await page.route("**/api/payments/*/verification", async (route) => {
    verificationBody = route.request().postDataJSON() as Record<
      string,
      unknown
    >;
    await route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "payment-cleared",
        kind: "updated",
        receipt: {
          ...pendingReceipt,
          allocatedAmount: "0.00",
          applicationState: "unapplied",
          availableForCoverage: "224.00",
          balanceVersion: 2,
          clearedAmount: "224.00",
          status: "cleared",
          unappliedAmount: "224.00",
          verifiedBy: "finance-verifier",
        },
      },
    });
  });
  await page.route("**/api/deliveries/*/cod-conversion", async (route) => {
    conversionBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      contentType: "application/json",
      json: {
        amount: "224.00",
        approved_by: "cod-credit-approver-mnl",
        conversion_id: "c44ab949-bfd5-43d7-98da-4ad153094465",
        currency: "PHP",
        delivery_id: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
        status: "approved",
      },
      status: 201,
    });
  });
  await page.route("**/api/payments/*/cash-reconciliation", async (route) => {
    reconciliationBody = route.request().postDataJSON() as Record<
      string,
      unknown
    >;
    await route.fulfill({
      contentType: "application/json",
      json: {
        cash_reconciliation_id: "b36f5035-c735-4a42-a781-40643532572d",
        counted_amount: "223.00",
        payment_receipt_id: pendingReceipt.paymentReceiptId,
        status: "reconciled",
        variance_amount: "-1.00",
      },
      status: 201,
    });
  });

  await page.goto("/payments");
  await expect(
    page.getByRole("heading", { name: "Make cleared money visible." }),
  ).toBeVisible();
  await expect(page.getByText("BANK-123")).toBeVisible();
  await expect(
    page.getByText("Every state says what happens next."),
  ).toBeVisible();
  await expect(page.getByText("Payment hold", { exact: true })).toBeVisible();

  await page.getByLabel("Branch ID").fill(pendingReceipt.branchId);
  await page.getByLabel("Customer Account ID").fill(pendingReceipt.customerId);
  await page
    .getByLabel("Sales Order ID optional")
    .fill(pendingReceipt.salesOrderId);
  await page.getByLabel("Received amount").fill("224.00");
  await page.getByRole("button", { name: "Record immutable receipt" }).click();
  await expect(
    page.getByRole("heading", { name: "Cleared payment" }).last(),
  ).toBeVisible();
  expect(recordedBody?.["idempotencyKey"]).toEqual(expect.any(String));

  await page.getByRole("button", { name: "Clear payment" }).click();
  await expect.poll(() => verificationBody).toBeDefined();
  expect(
    (
      verificationBody?.["command"] as {
        decision: string;
      }
    ).decision,
  ).toBe("cleared");

  await page
    .getByLabel("Delivery ID")
    .fill("8a8e9f4d-cb22-4c51-9fd7-30995bf9abef");
  await page
    .getByLabel("Credit Override reason")
    .fill("Customer accepted delivery under approved account exception");
  await page.getByRole("button", { name: "Approve COD conversion" }).click();
  await expect(
    page.getByText(/Converted PHP 224.00 to On Account/),
  ).toBeVisible();
  expect(conversionBody?.["idempotencyKey"]).toEqual(expect.any(String));

  await page
    .getByLabel("Cash Payment Receipt ID")
    .fill(pendingReceipt.paymentReceiptId);
  await page.getByLabel("Counted cash").fill("223.00");
  await page
    .getByLabel("Reconciliation or discrepancy reason")
    .fill("One peso documented pouch shortage");
  await page.getByRole("button", { name: "Reconcile COD cash" }).click();
  await expect(
    page.getByText("Cash reconciled; recorded variance PHP -1.00."),
  ).toBeVisible();
  expect(reconciliationBody?.["idempotencyKey"]).toEqual(expect.any(String));
});

test("keeps validation local before posting incomplete transfer evidence", async ({
  page,
}) => {
  await page.route("**/api/payments?status=pending_verification", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: {
        correlationId: "empty-payment-queue",
        items: [],
        kind: "ready",
        total: 0,
      },
    }),
  );
  let postCount = 0;
  await page.route("**/api/payments", (route) => {
    postCount += 1;
    return route.abort();
  });
  await page.goto("/payments");
  await page.getByRole("button", { name: "bank transfer" }).click();
  await page.getByLabel("Branch ID").fill(pendingReceipt.branchId);
  await page.getByLabel("Customer Account ID").fill(pendingReceipt.customerId);
  await page.getByLabel("Received amount").fill("224.00");
  await page.getByRole("button", { name: "Record immutable receipt" }).click();
  await expect(
    page.getByText(/Non-cash receipts require the external reference/),
  ).toBeVisible();
  expect(postCount).toBe(0);
});

test("retains COD command identities and gives retry guidance after network loss", async ({
  page,
}) => {
  await page.route("**/api/payments?status=pending_verification", (route) =>
    route.fulfill({
      contentType: "application/json",
      json: { correlationId: "empty", items: [], kind: "ready", total: 0 },
    }),
  );
  const conversions: Array<Record<string, unknown>> = [];
  const reconciliations: Array<Record<string, unknown>> = [];
  await page.route("**/api/deliveries/*/cod-conversion", async (route) => {
    conversions.push(route.request().postDataJSON() as Record<string, unknown>);
    if (conversions.length === 1) return route.abort();
    return route.fulfill({
      contentType: "application/json",
      json: { amount: "224.00", status: "approved" },
      status: 201,
    });
  });
  await page.route("**/api/payments/*/cash-reconciliation", async (route) => {
    reconciliations.push(
      route.request().postDataJSON() as Record<string, unknown>,
    );
    if (reconciliations.length === 1) return route.abort();
    return route.fulfill({
      contentType: "application/json",
      json: { variance_amount: "0.00" },
      status: 201,
    });
  });
  await page.goto("/payments");
  await page
    .getByLabel("Delivery ID")
    .fill("8a8e9f4d-cb22-4c51-9fd7-30995bf9abef");
  await page
    .getByLabel("Credit Override reason")
    .fill("Approved account exception");
  await page.getByRole("button", { name: "Approve COD conversion" }).click();
  await expect(
    page.getByText(/COD conversion service unavailable.*identity is retained/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approve COD conversion" }).click();
  expect(conversions[1]?.["idempotencyKey"]).toBe(
    conversions[0]?.["idempotencyKey"],
  );

  await page
    .getByLabel("Cash Payment Receipt ID")
    .fill(pendingReceipt.paymentReceiptId);
  await page.getByLabel("Counted cash").fill("224.00");
  await page
    .getByLabel("Reconciliation or discrepancy reason")
    .fill("Counted and sealed");
  await page.getByRole("button", { name: "Reconcile COD cash" }).click();
  await expect(
    page.getByText(
      /Cash reconciliation service unavailable.*identity is retained/,
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "Reconcile COD cash" }).click();
  expect(reconciliations[1]?.["idempotencyKey"]).toBe(
    reconciliations[0]?.["idempotencyKey"],
  );
});
