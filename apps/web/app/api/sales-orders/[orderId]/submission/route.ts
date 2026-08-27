import { getServerApiConfig } from "@/lib/server-api";
import { submitSalesOrderDraft } from "@tradeflow/sales-order-draft";
import { NextRequest } from "next/server";

type RequestBody = {
  expectedVersion: number;
  idempotencyKey: string;
};

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ orderId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const body = (await request.json()) as RequestBody;
  const { orderId } = await context.params;
  const state = await submitSalesOrderDraft({
    accessToken: getServerApiConfig().accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
    expectedVersion: body.expectedVersion,
    idempotencyKey: body.idempotencyKey,
    salesOrderId: orderId,
  });
  const status =
    state.kind === "unauthenticated"
      ? 401
      : state.kind === "forbidden"
        ? 403
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
