import { createTradeFlowClient } from "@tradeflow/api-client";

export const dynamic = "force-dynamic";

function failureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  return "unavailable";
}

export async function GET(
  request: Request,
  context: { params: Promise<{ purchaseOrderId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { purchaseOrderId } = await context.params;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
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
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId,
  });

  try {
    const result = await client.GET(
      "/v1/procurement/purchase-orders/{purchase_order_id}",
      {
        params: { path: { purchase_order_id: purchaseOrderId } },
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
        code: envelope.error?.code ?? "purchase_order_unavailable",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Purchase order was not returned.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "purchase_order_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Purchase order service unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}
