import { listPicks } from "@tradeflow/warehouse-picking";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "not_found") return 404;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ fulfillmentOrderId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { fulfillmentOrderId } = await context.params;
  const state = await listPicks({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl,
    correlationId,
    fulfillmentOrderId,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
