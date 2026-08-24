import { getServerApiConfig } from "@/lib/server-api";
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
    accessToken: getServerApiConfig().accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId: crypto.randomUUID(),
    query: request.nextUrl.searchParams.get("query") ?? "",
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store" },
    status: statusByKind[state.kind],
  });
}
