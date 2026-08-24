import { getServerApiConfig } from "@/lib/server-api";
import {
  createSupplier,
  searchSuppliers,
  type CreateSupplierInput,
} from "@tradeflow/procurement-suppliers";

const baseUrl = getServerApiConfig().baseUrl;

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const url = new URL(request.url);
  const state = await searchSuppliers({
    accessToken: getServerApiConfig().accessToken,
    baseUrl,
    correlationId,
    limit: parseInt(url.searchParams.get("limit") ?? "25", 10),
    offset: parseInt(url.searchParams.get("offset") ?? "0", 10),
    query: url.searchParams.get("query") ?? "",
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}

export async function POST(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const command = (await request.json()) as CreateSupplierInput;
  const state = await createSupplier({
    accessToken: getServerApiConfig().accessToken,
    baseUrl,
    command,
    correlationId,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
