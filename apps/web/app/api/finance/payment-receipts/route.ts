import { createTradeFlowClient } from "@tradeflow/api-client";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

export async function GET(): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before viewing payment receipts.",
      },
      { status: 401 },
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl,
    correlationId,
  });
  try {
    const result = await client.GET("/v1/finance/payment-receipts", {
      params: { query: { status: "cleared", limit: 100 } },
    });
    if (result.data !== undefined) {
      return Response.json(result.data, {
        headers: {
          "Cache-Control": "no-store",
          "X-Correlation-ID": correlationId,
        },
        status: result.response.status,
      });
    }
    const envelope = (result.error ?? {}) as {
      error?: { code?: string; correlation_id?: string; message?: string };
    };
    return Response.json(
      {
        code: envelope.error?.code ?? "receipt_list_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Receipt list is unavailable.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "receipt_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Receipt service is unavailable; retry later.",
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
