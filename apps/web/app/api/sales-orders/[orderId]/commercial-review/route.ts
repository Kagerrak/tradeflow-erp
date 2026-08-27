import { getServerApiConfig } from "@/lib/server-api";
import { loadCommercialReview } from "@tradeflow/sales-order-draft";
import { NextRequest } from "next/server";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ orderId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { orderId } = await context.params;
  const warehouseId = request.nextUrl.searchParams.get("warehouse_id") ?? "";
  const state =
    warehouseId.length === 0
      ? ({ correlationId, kind: "validation" } as const)
      : await loadCommercialReview({
          accessToken: getServerApiConfig().accessToken,
          baseUrl: getServerApiConfig().baseUrl,
          correlationId,
          salesOrderId: orderId,
          warehouseId,
        });
  const status =
    state.kind === "unauthenticated"
      ? 401
      : state.kind === "forbidden"
        ? 403
        : state.kind === "not_found"
          ? 404
          : state.kind === "validation"
            ? 422
            : state.kind === "conflict"
              ? 409
              : state.kind === "unavailable"
                ? 503
                : 200;
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status,
  });
}
