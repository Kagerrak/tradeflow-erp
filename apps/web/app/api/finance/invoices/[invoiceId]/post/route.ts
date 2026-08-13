import { createTradeFlowClient } from "@tradeflow/api-client";

type Body = {
  idempotencyKey: string;
};

export async function POST(
  request: Request,
  context: { params: Promise<{ invoiceId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { invoiceId } = await context.params;
  const body = (await request.json()) as Body;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before posting an invoice.",
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
      "/v1/finance/invoices/{draft_invoice_id}/post",
      {
        body: {},
        headers: { "Idempotency-Key": body.idempotencyKey },
        params: { path: { draft_invoice_id: invoiceId } },
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
        code: envelope.error?.code ?? "invoice_post_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Invoice was not posted.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "invoice_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Invoice posting is unavailable; retry unchanged work.",
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
