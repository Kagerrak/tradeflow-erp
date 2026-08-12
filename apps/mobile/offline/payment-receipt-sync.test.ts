import {
  createMemoryPaymentReceiptStore,
  type PaymentReceiptStore,
} from "./payment-receipt-store";
import { syncPaymentReceipts } from "./payment-receipt-sync";

const command = {
  amount: "224.00",
  branch_id: "efad4205-5060-49fb-b752-3faca649ca6e",
  currency: "PHP",
  customer_id: "98481a1c-e493-41a6-851b-93142553ceab",
  evidence: null,
  external_reference: null,
  payment_method: "cash" as const,
  payment_receipt_id: "d2528c7a-c76a-42b1-a427-cde44d61f0b4",
  received_at: "2026-07-29T02:00:00Z",
  sales_order_id: null,
};

async function queuedStore(): Promise<PaymentReceiptStore> {
  const store = createMemoryPaymentReceiptStore();
  await store.saveAndEnqueue(
    command,
    "stable-mobile-payment",
    "2026-07-29T02:00:00Z",
  );
  return store;
}

it("keeps a failed durable command and reuses its identity on retry", async () => {
  const store = await queuedStore();
  const seenKeys: string[] = [];
  let attempt = 0;
  const fetch = async (request: Request) => {
    seenKeys.push(request.headers.get("Idempotency-Key") ?? "");
    attempt += 1;
    if (attempt === 1) return new Response("{}", { status: 503 });
    return new Response(
      JSON.stringify({
        amount: "224.00",
        available_for_coverage: "224.00",
        branch_id: command.branch_id,
        cash_reconciliation_status: "unreconciled",
        cleared_amount: "224.00",
        currency: "PHP",
        customer_id: command.customer_id,
        external_reference: null,
        external_reference_normalized: null,
        payment_method: "cash",
        payment_receipt_id: command.payment_receipt_id,
        received_at: command.received_at,
        recorded_by: "route-collector",
        reversal_id: null,
        sales_order_id: null,
        status: "cleared",
        unapplied_amount: "224.00",
        verified_by: null,
      }),
      {
        headers: { "content-type": "application/json" },
        status: 201,
      },
    );
  };
  const common = {
    accessToken: "token",
    baseUrl: "https://api.test",
    createCorrelationId: () => "mobile-payment-sync",
    fetch,
    store,
  };
  const paused = await syncPaymentReceipts(common);
  expect(paused.kind).toBe("paused");
  expect(await store.listPending()).toHaveLength(1);

  const synced = await syncPaymentReceipts(common);
  expect(synced.kind).toBe("synced");
  expect(await store.listPending()).toHaveLength(0);
  expect(seenKeys).toEqual(["stable-mobile-payment", "stable-mobile-payment"]);
});
