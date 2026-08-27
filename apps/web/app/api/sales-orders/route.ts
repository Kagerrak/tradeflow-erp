import { getServerApiConfig } from "@/lib/server-api";
import {
  createSalesOrderDraft,
  searchSalesOrders,
  type CreateSalesOrderDraftInput,
} from "@tradeflow/sales-order-draft";

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const query = new URL(request.url).searchParams.get("query") ?? "";
  const state = await searchSalesOrders({
    accessToken: getServerApiConfig().accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
    query,
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

type RequestBody = {
  command: CreateSalesOrderDraftInput;
  idempotencyKey: string;
};

export async function POST(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const body = (await request.json()) as RequestBody;
  const state = await createSalesOrderDraft({
    accessToken: getServerApiConfig().accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    command: body.command,
    correlationId,
    idempotencyKey: body.idempotencyKey,
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
