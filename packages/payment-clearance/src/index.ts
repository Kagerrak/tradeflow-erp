import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type PaymentReceiptStatus =
  | "pending_verification"
  | "awaiting_bank_clearance"
  | "cleared"
  | "rejected"
  | "reversed";

export type PaymentOperationalState =
  | PaymentReceiptStatus
  | "insufficient"
  | "deadline_due"
  | "payment_hold"
  | "retry_ready";

export const paymentStateContent: Record<
  PaymentOperationalState,
  {
    description: string;
    nextAction: string;
    title: string;
    tone: "attention" | "critical" | "neutral" | "positive";
  }
> = {
  awaiting_bank_clearance: {
    description:
      "Finance verified the check evidence. It is not cleared money until the bank confirms settlement.",
    nextAction:
      "Record bank-clearance evidence with a distinct authorized checker.",
    title: "Awaiting bank clearance",
    tone: "attention",
  },
  cleared: {
    description:
      "Method-specific controls passed. The amount remains unapplied until an invoice allocation is posted.",
    nextAction: "Review reserved-value coverage before releasing the pick.",
    title: "Cleared payment",
    tone: "positive",
  },
  deadline_due: {
    description:
      "The prepaid reservation is still short and its Branch deadline has arrived.",
    nextAction: "Clear sufficient payment now or process the deadline release.",
    title: "Payment deadline due",
    tone: "attention",
  },
  insufficient: {
    description:
      "Cleared prepayment does not yet equal the value of the quantity reserved for this fulfillment.",
    nextAction: "Record and clear only the remaining reserved-value shortfall.",
    title: "Coverage is short",
    tone: "attention",
  },
  payment_hold: {
    description:
      "The unpaid reservation was released to backorder without cancelling the Sales Order.",
    nextAction: "Clear payment, then run a new successful reservation retry.",
    title: "Payment hold",
    tone: "critical",
  },
  pending_verification: {
    description:
      "The receipt and evidence are recorded, but this amount is not cleared money.",
    nextAction:
      "A different eligible Finance Staff user must verify the evidence.",
    title: "Pending verification",
    tone: "neutral",
  },
  rejected: {
    description:
      "Finance rejected the evidence. The immutable receipt remains visible and the reference may be reused.",
    nextAction:
      "Record a new receipt with corrected evidence if payment was received.",
    title: "Evidence rejected",
    tone: "critical",
  },
  retry_ready: {
    description:
      "Payment is available, but an expired reservation cannot be revived in place.",
    nextAction:
      "Retry reservation; a fresh Fulfillment Order will receive coverage.",
    title: "Ready to reserve again",
    tone: "positive",
  },
  reversed: {
    description:
      "The cleared receipt was reversed. Its original record and event history remain immutable.",
    nextAction:
      "Investigate the source payment; use refund only for money returned to the customer.",
    title: "Payment reversed",
    tone: "critical",
  },
};

export type RecordPaymentReceiptInput =
  components["schemas"]["RecordPaymentReceiptCommand"];
export type PaymentVerificationInput =
  components["schemas"]["PaymentVerificationCommand"];

export type PaymentReceipt = {
  amount: string;
  availableForCoverage: string;
  branchId: string;
  cashReconciliationStatus: string | null;
  clearedAmount: string;
  currency: string;
  customerId: string;
  externalReference: string | null;
  externalReferenceNormalized: string | null;
  paymentMethod: string;
  paymentReceiptId: string;
  receivedAt: string;
  recordedBy: string;
  reversalId: string | null;
  salesOrderId: string | null;
  status: PaymentReceiptStatus;
  unappliedAmount: string;
  verifiedBy: string | null;
};

type FailureKind =
  "conflict" | "forbidden" | "unauthenticated" | "unavailable" | "validation";

export type PaymentReceiptListState =
  | { correlationId: string; kind: FailureKind }
  | {
      correlationId: string;
      items: PaymentReceipt[];
      kind: "ready";
      total: number;
    };

export type PaymentReceiptCommandState =
  | { correlationId: string; kind: FailureKind }
  | {
      correlationId: string;
      kind: "recorded" | "updated";
      receipt: PaymentReceipt;
    };

type ClientInput = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
};

function failureKind(status: number): FailureKind {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  return "unavailable";
}

function mapReceipt(
  value: components["schemas"]["PaymentReceiptResponse"],
): PaymentReceipt {
  return {
    amount: value.amount,
    availableForCoverage: value.available_for_coverage,
    branchId: value.branch_id,
    cashReconciliationStatus: value.cash_reconciliation_status,
    clearedAmount: value.cleared_amount,
    currency: value.currency,
    customerId: value.customer_id,
    externalReference: value.external_reference,
    externalReferenceNormalized: value.external_reference_normalized,
    paymentMethod: value.payment_method,
    paymentReceiptId: value.payment_receipt_id,
    receivedAt: value.received_at,
    recordedBy: value.recorded_by,
    reversalId: value.reversal_id,
    salesOrderId: value.sales_order_id,
    status: value.status as PaymentReceiptStatus,
    unappliedAmount: value.unapplied_amount,
    verifiedBy: value.verified_by,
  };
}

function clientFor(input: ClientInput) {
  if (input.accessToken === undefined || input.accessToken.length === 0) {
    return null;
  }
  return createTradeFlowClient({
    accessToken: input.accessToken,
    baseUrl: input.baseUrl,
    correlationId: input.correlationId,
    ...(input.fetch === undefined ? {} : { fetch: input.fetch }),
  });
}

export async function listPaymentReceipts(
  input: ClientInput & {
    branchId?: string;
    status?: PaymentReceiptStatus;
  },
): Promise<PaymentReceiptListState> {
  const client = clientFor(input);
  if (client === null) {
    return { correlationId: input.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await client.GET(
      "/v1/finance/payment-receipts",
      {
        params: {
          query: {
            ...(input.branchId === undefined
              ? {}
              : { branch_id: input.branchId }),
            ...(input.status === undefined ? {} : { status: input.status }),
          },
        },
      },
    );
    if (data === undefined) {
      return {
        correlationId: input.correlationId,
        kind: failureKind(response.status),
      };
    }
    return {
      correlationId: input.correlationId,
      items: data.items.map(mapReceipt),
      kind: "ready",
      total: data.total,
    };
  } catch {
    return { correlationId: input.correlationId, kind: "unavailable" };
  }
}

export async function recordPaymentReceipt(
  input: ClientInput & {
    command: RecordPaymentReceiptInput;
    idempotencyKey: string;
  },
): Promise<PaymentReceiptCommandState> {
  const client = clientFor(input);
  if (client === null) {
    return { correlationId: input.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await client.POST(
      "/v1/finance/payment-receipts",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
      },
    );
    if (data === undefined) {
      return {
        correlationId: input.correlationId,
        kind: failureKind(response.status),
      };
    }
    return {
      correlationId: input.correlationId,
      kind: "recorded",
      receipt: mapReceipt(data),
    };
  } catch {
    return { correlationId: input.correlationId, kind: "unavailable" };
  }
}

export async function verifyPaymentReceipt(
  input: ClientInput & {
    command: PaymentVerificationInput;
    idempotencyKey: string;
    paymentReceiptId: string;
  },
): Promise<PaymentReceiptCommandState> {
  const client = clientFor(input);
  if (client === null) {
    return { correlationId: input.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await client.POST(
      "/v1/finance/payment-receipts/{payment_receipt_id}/verification",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
        params: { path: { payment_receipt_id: input.paymentReceiptId } },
      },
    );
    if (data === undefined) {
      return {
        correlationId: input.correlationId,
        kind: failureKind(response.status),
      };
    }
    return {
      correlationId: input.correlationId,
      kind: "updated",
      receipt: mapReceipt(data),
    };
  } catch {
    return { correlationId: input.correlationId, kind: "unavailable" };
  }
}
