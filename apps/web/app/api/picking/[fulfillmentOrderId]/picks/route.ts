import { getServerApiConfig } from "@/lib/server-api";
import { listPicks } from "@tradeflow/warehouse-picking";

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

export async function GET(
  _request: Request,
  context: { params: Promise<{ fulfillmentOrderId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { fulfillmentOrderId } = await context.params;
  const state = await listPicks({
    accessToken: getServerApiConfig().accessToken,
    baseUrl,
    correlationId,
    fulfillmentOrderId,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
