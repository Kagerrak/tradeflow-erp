import {
  loadSalesOrderDraft,
  type UpdateSalesOrderDraftInput,
  updateSalesOrderDraft,
} from "@tradeflow/sales-order-draft";
import { NextRequest } from "next/server";

type RequestBody = {
  command: UpdateSalesOrderDraftInput;
  expectedVersion: number;
  idempotencyKey: string;
};

export async function GET(
  _: NextRequest,
  context: { params: Promise<{ orderId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { orderId } = await context.params;
  const state = await loadSalesOrderDraft({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId,
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

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ orderId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const body = (await request.json()) as RequestBody;
  const { orderId } = await context.params;
  const state = await updateSalesOrderDraft({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
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
