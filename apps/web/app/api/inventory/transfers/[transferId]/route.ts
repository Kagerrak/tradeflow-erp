import { createTradeFlowClient } from "@tradeflow/api-client";

import { transferFailureKind } from "../response";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

export async function GET(
  _request: Request,
  context: { params: Promise<{ transferId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { transferId } = await context.params;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before viewing transfer details.",
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
    const result = await client.GET("/v1/inventory/transfers/{transfer_id}", {
      params: { path: { transfer_id: transferId } },
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
        code: envelope.error?.code ?? "transfer_detail_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: transferFailureKind(result.response.status),
        message: envelope.error?.message ?? "Transfer detail is unavailable.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "transfer_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Transfer service is unavailable; retry later.",
      },
      { status: 503 },
    );
  }
}
