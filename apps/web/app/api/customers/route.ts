import {
  createCustomerAccount,
  searchCustomerDirectory,
  type CreateCustomerAccountInput,
} from "@tradeflow/customer-directory";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const statusByKind = {
  conflict: 409,
  created: 201,
  forbidden: 403,
  ready: 200,
  unauthenticated: 401,
  unavailable: 503,
  validation: 422,
} as const;

const serviceOptions = () => ({
  accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
  baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
  correlationId: crypto.randomUUID(),
});

export async function GET(request: NextRequest): Promise<Response> {
  const state = await searchCustomerDirectory({
    ...serviceOptions(),
    query: request.nextUrl.searchParams.get("query") ?? "",
  });
  return Response.json(state, { status: statusByKind[state.kind] });
}

export async function POST(request: NextRequest): Promise<Response> {
  const command = (await request.json()) as CreateCustomerAccountInput;
  const state = await createCustomerAccount({
    ...serviceOptions(),
    command,
    idempotencyKey:
      request.headers.get("Idempotency-Key") ?? crypto.randomUUID(),
  });
  return Response.json(state, { status: statusByKind[state.kind] });
}
