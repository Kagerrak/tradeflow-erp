import { describe, expect, it } from "vitest";

import {
  listPaymentReceipts,
  paymentStateContent,
  recordPaymentReceipt,
} from "./index";

const receipt = {
  amount: "224.00",
  available_for_coverage: "224.00",
  branch_id: "a22641c5-ae42-403f-8465-0665efd09110",
  cash_reconciliation_status: null,
  cleared_amount: "0.00",
  currency: "PHP",
  customer_id: "6fc13812-dc64-43dd-874e-f3b3e2728dcc",
  external_reference: "BANK-123",
  external_reference_normalized: "BANK-123",
  payment_method: "bank_transfer",
  payment_receipt_id: "d2528c7a-c76a-42b1-a427-cde44d61f0b4",
  received_at: "2026-07-29T02:00:00Z",
  recorded_by: "finance-recorder",
  reversal_id: null,
  sales_order_id: "99596045-e62d-46b4-8521-739f0bde2359",
  status: "pending_verification",
  unapplied_amount: "0.00",
  verified_by: null,
};

describe("Payment Clearance client", () => {
  it("gives every operational state an explicit next action", () => {
    expect(Object.keys(paymentStateContent)).toHaveLength(14);
    for (const content of Object.values(paymentStateContent)) {
      expect(content.title.length).toBeGreaterThan(0);
      expect(content.nextAction.length).toBeGreaterThan(0);
    }
    expect(paymentStateContent.payment_hold.nextAction).toContain(
      "reservation",
    );
    expect(paymentStateContent.reversed.tone).toBe("critical");
  });

  it("maps the scoped verification queue without losing money precision", async () => {
    const state = await listPaymentReceipts({
      accessToken: "token",
      baseUrl: "https://api.test",
      branchId: receipt.branch_id,
      correlationId: "payment-list",
      fetch: () =>
        Promise.resolve(
          new Response(JSON.stringify({ items: [receipt], total: 1 }), {
            headers: { "content-type": "application/json" },
            status: 200,
          }),
        ),
      status: "pending_verification",
    });
    expect(state).toEqual({
      correlationId: "payment-list",
      items: [
        expect.objectContaining({
          amount: "224.00",
          externalReferenceNormalized: "BANK-123",
          status: "pending_verification",
        }),
      ],
      kind: "ready",
      total: 1,
    });
  });

  it("keeps the same idempotency identity for receipt retries", async () => {
    let captured: Request | undefined;
    const state = await recordPaymentReceipt({
      accessToken: "token",
      baseUrl: "https://api.test",
      command: {
        amount: "224.00",
        branch_id: receipt.branch_id,
        currency: "PHP",
        customer_id: receipt.customer_id,
        evidence: null,
        external_reference: null,
        payment_method: "cash",
        payment_receipt_id: receipt.payment_receipt_id,
        received_at: "2026-07-29T02:00:00Z",
        sales_order_id: receipt.sales_order_id,
      },
      correlationId: "payment-record",
      fetch: (request) => {
        captured = request;
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ...receipt,
              cash_reconciliation_status: "unreconciled",
              cleared_amount: "224.00",
              payment_method: "cash",
              status: "cleared",
              unapplied_amount: "224.00",
            }),
            {
              headers: { "content-type": "application/json" },
              status: 201,
            },
          ),
        );
      },
      idempotencyKey: "stable-payment-command",
    });
    expect(captured?.headers.get("Idempotency-Key")).toBe(
      "stable-payment-command",
    );
    expect(state.kind).toBe("recorded");
  });
});
