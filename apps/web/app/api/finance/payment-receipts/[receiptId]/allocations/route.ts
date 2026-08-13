import { createTradeFlowClient } from "@tradeflow/api-client";

type Body = {
  invoiceId: string;
  amount: string;
  idempotencyKey: string;
};

export async function POST(
  request: Request,
  context: { params: Promise<{ receiptId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { receiptId } = await context.params;
  const body = (await request.json()) as Body;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before allocating a payment receipt.",
      },
      { status: 401 },
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId,
  });
  try {
    const result = await client.POST(
      "/v1/finance/payment-receipts/{payment_receipt_id}/allocations",
      {
        body: {
          allocations: [{ amount: body.amount, invoice_id: body.invoiceId }],
        },
        headers: { "Idempotency-Key": body.idempotencyKey },
        params: { path: { payment_receipt_id: receiptId } },
      },
    );
    if (result.data !== undefined) {
      return Response.json(result.data, {
        headers: { "X-Correlation-ID": correlationId },
        status: result.response.status,
      });
    }
    const envelope = (result.error ?? {}) as {
      error?: { code?: string; correlation_id?: string; message?: string };
    };
    return Response.json(
      {
        code: envelope.error?.code ?? "allocation_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Allocation was not accepted.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "allocation_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Allocation is unavailable; retry unchanged work.",
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
