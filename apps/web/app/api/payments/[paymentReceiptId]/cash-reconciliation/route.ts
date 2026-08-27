import { getServerApiConfig } from "@/lib/server-api";
import { createTradeFlowClient, type components } from "@tradeflow/api-client";

type Body = {
  command: components["schemas"]["CashReconciliationCommand"];
  idempotencyKey: string;
};

export async function POST(
  request: Request,
  context: { params: Promise<{ paymentReceiptId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { paymentReceiptId } = await context.params;
  const body = (await request.json()) as Body;
  const accessToken = getServerApiConfig().accessToken;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
      },
      { status: 401 },
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
  });
  try {
    const result = await client.POST(
      "/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation",
      {
        body: body.command,
        headers: { "Idempotency-Key": body.idempotencyKey },
        params: { path: { payment_receipt_id: paymentReceiptId } },
      },
    );
    if (result.data !== undefined) {
      return Response.json(result.data, { status: result.response.status });
    }
    const envelope = (result.error ?? {}) as {
      error?: { code?: string; correlation_id?: string; message?: string };
    };
    return Response.json(
      {
        code: envelope.error?.code ?? "cash_reconciliation_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message:
          envelope.error?.message ?? "Cash reconciliation was not accepted.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "payment_service_unavailable",
        correlationId,
        kind: "unavailable",
        message:
          "Cash reconciliation outcome is unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}

function failureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  return "unavailable";
}
