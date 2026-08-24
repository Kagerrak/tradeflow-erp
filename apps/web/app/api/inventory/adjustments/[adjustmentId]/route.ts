import { getServerApiConfig } from "@/lib/server-api";
import { createTradeFlowClient } from "@tradeflow/api-client";

import { adjustmentFailureKind } from "../response";

const baseUrl = getServerApiConfig().baseUrl;

export async function GET(
  _request: Request,
  context: { params: Promise<{ adjustmentId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { adjustmentId } = await context.params;
  const accessToken = getServerApiConfig().accessToken;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before viewing adjustment details.",
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
    const result = await client.GET(
      "/v1/inventory/adjustments/{adjustment_id}",
      {
        params: { path: { adjustment_id: adjustmentId } },
      },
    );
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
        code: envelope.error?.code ?? "adjustment_detail_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: adjustmentFailureKind(result.response.status),
        message: envelope.error?.message ?? "Adjustment detail is unavailable.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "adjustment_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Adjustment service is unavailable; retry later.",
      },
      { status: 503 },
    );
  }
}
