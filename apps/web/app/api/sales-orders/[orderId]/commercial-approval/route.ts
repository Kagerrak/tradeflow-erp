import { getServerApiConfig } from "@/lib/server-api";
import {
  commerciallyApproveSalesOrder,
  type CommercialApprovalInput,
} from "@tradeflow/sales-order-draft";
import { NextRequest } from "next/server";

type RequestBody = {
  command: CommercialApprovalInput;
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
  const state = await commerciallyApproveSalesOrder({
    accessToken: getServerApiConfig().accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    command: body.command,
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
          : state.kind === "conflict" ||
              state.kind === "exception_required" ||
              state.kind === "held"
            ? 409
            : state.kind === "unavailable"
              ? 503
              : 200;
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status,
  });
}
