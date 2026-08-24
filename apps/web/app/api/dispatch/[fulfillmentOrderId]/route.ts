import { getServerApiConfig } from "@/lib/server-api";
import {
  dispatchFulfillment,
  type DispatchCommand,
} from "@tradeflow/delivery-dispatch";

const baseUrl = getServerApiConfig().baseUrl;

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "not_found") return 404;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

type RouteContext = {
  params: Promise<{ fulfillmentOrderId: string }>;
};

type PostBody = {
  command: DispatchCommand;
  idempotencyKey: string;
};

export async function POST(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { fulfillmentOrderId } = await context.params;
  const body = (await request.json()) as PostBody;
  const state = await dispatchFulfillment({
    accessToken: getServerApiConfig().accessToken,
    baseUrl,
    command: body.command,
    correlationId,
    fulfillmentOrderId,
    idempotencyKey: body.idempotencyKey,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
