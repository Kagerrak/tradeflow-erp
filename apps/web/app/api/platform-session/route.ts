import { loadPlatformSession } from "@tradeflow/platform-session";

export const dynamic = "force-dynamic";

const statusByState = {
  forbidden: 403,
  ready: 200,
  unauthenticated: 401,
  unavailable: 503,
} as const;

export async function GET(): Promise<Response> {
  const correlationId = crypto.randomUUID();

  try {
    const state = await loadPlatformSession({
      accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
      baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
      correlationId,
    });

    return Response.json(state, {
      headers: {
        "Cache-Control": "no-store",
        "X-Correlation-ID": correlationId,
      },
      status: statusByState[state.kind],
    });
  } catch {
    return Response.json(
      {
        correlationId,
        kind: "unavailable",
      },
      {
        headers: {
          "Cache-Control": "no-store",
          "X-Correlation-ID": correlationId,
        },
        status: 503,
      },
    );
  }
}
