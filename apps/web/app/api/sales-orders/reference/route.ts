import { getServerApiConfig } from "@/lib/server-api";
import { loadOrderEntryReference } from "@tradeflow/sales-order-draft";
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const accessToken = getServerApiConfig().accessToken;
  const branchId = request.nextUrl.searchParams.get("branchId");
  const customerId = request.nextUrl.searchParams.get("customerId");
  if (branchId === null || customerId === null) {
    return Response.json(
      { correlationId, kind: "validation" },
      { status: 422 },
    );
  }
  const state = await loadOrderEntryReference({
    accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    branchId,
    correlationId,
    customerId,
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
