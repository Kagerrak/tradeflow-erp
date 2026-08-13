import { createTradeFlowClient } from "@tradeflow/api-client";

export const dynamic = "force-dynamic";

function failureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  return "unavailable";
}

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const url = new URL(request.url);
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
    const params: Record<string, unknown> = {};
    const limit = url.searchParams.get("limit");
    const offset = url.searchParams.get("offset");
    if (limit !== null) {
      params.limit = Number(limit);
    }
    if (offset !== null) {
      params.offset = Number(offset);
    }
    const result = await client.GET(
      "/v1/procurement/purchase-orders/receipts",
      {
        params: { query: params },
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
        code: envelope.error?.code ?? "goods_receipts_unavailable",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Goods receipts were not returned.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "goods_receipts_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Goods receipt service unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}
