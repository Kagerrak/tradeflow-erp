import { getServerApiConfig } from "@/lib/server-api";
import { listAssignedDeliveries } from "@tradeflow/delivery-dispatch";

export async function GET(): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const state = await listAssignedDeliveries({
    accessToken: getServerApiConfig().accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}
