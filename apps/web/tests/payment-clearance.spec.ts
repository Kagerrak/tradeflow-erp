import { expect, test } from "@playwright/test";

const pendingReceipt = {
  amount: "224.00",
  availableForCoverage: "0.00",
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
          availableForCoverage: "224.00",
          clearedAmount: "224.00",
          status: "cleared",
          unappliedAmount: "224.00",
          verifiedBy: "finance-verifier",
        },
      },
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
