import { authorizationHeaders, getServerApiConfig } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const config = getServerApiConfig();
  const incoming = new URL(request.url);
  const target = new URL("/v1/operations/overview", config.baseUrl);
  for (const key of ["branch_id", "from_date", "to_date"]) {
    const value = incoming.searchParams.get(key);
    if (value) target.searchParams.set(key, value);
  }
  try {
    const response = await fetch(target, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...authorizationHeaders(),
        "X-Correlation-ID": correlationId,
      },
    });
    return new Response(await response.text(), {
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": "application/json",
        "X-Correlation-ID":
          response.headers.get("X-Correlation-ID") ?? correlationId,
      },
      status: response.status,
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "operations_overview_unavailable",
          correlation_id: correlationId,
          message: "Operational data is temporarily unavailable.",
        },
      },
      { status: 503 },
    );
  }
}
