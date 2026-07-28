import {
  searchInventoryDirectory,
  type InventoryDirectoryState,
} from "@tradeflow/inventory-directory";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const statusByKind: Record<InventoryDirectoryState["kind"], number> = {
  forbidden: 403,
  ready: 200,
  unauthenticated: 401,
  unavailable: 503,
  validation: 422,
};

export async function GET(request: NextRequest): Promise<Response> {
  const state = await searchInventoryDirectory({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId: crypto.randomUUID(),
    query: request.nextUrl.searchParams.get("query") ?? "",
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store" },
    status: statusByKind[state.kind],
  });
}
