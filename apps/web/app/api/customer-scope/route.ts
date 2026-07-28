import { createTradeFlowClient } from "@tradeflow/api-client";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      { correlationId, kind: "unauthenticated" },
      { status: 401 },
    );
  }

  try {
    const client = createTradeFlowClient({
      accessToken,
      baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
      correlationId,
    });
    const { data, response } = await client.GET("/v1/organization/scope");
    if (data !== undefined) {
      return Response.json(data, {
        headers: {
          "Cache-Control": "no-store",
          "X-Correlation-ID": correlationId,
        },
      });
    }
    return Response.json(
      {
        correlationId,
        kind: response.status === 401 ? "unauthenticated" : "forbidden",
      },
      { status: response.status },
    );
  } catch {
    return Response.json(
      { correlationId, kind: "unavailable" },
      { status: 503 },
    );
  }
}
