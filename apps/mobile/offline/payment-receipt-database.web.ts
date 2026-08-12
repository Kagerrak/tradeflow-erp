import type { PaymentReceiptStore } from "./payment-receipt-store";

export async function createPaymentReceiptStore(
  _databaseName?: string,
): Promise<PaymentReceiptStore> {
  throw new Error(
    "Durable offline Payment Receipt capture requires iOS or Android.",
  );
}
