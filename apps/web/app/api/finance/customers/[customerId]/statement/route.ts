import { getServerApiConfig } from "@/lib/server-api";
import { createTradeFlowClient } from "@tradeflow/api-client";

export async function GET(
  request: Request,
  context: { params: Promise<{ customerId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { customerId } = await context.params;
  const { searchParams } = new URL(request.url);
  const fromDate = searchParams.get("from_date");
  const toDate = searchParams.get("to_date");
  const asOf = searchParams.get("as_of");
  const accessToken = getServerApiConfig().accessToken;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before viewing a customer statement.",
      },
      { status: 401 },
    );
  }
  if (fromDate === null || toDate === null) {
    return Response.json(
      {
        code: "validation_required",
        correlationId,
        kind: "validation",
        message: "from_date and to_date are required.",
      },
      { status: 400 },
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
  });
  try {
    const result = await client.GET(
      "/v1/finance/customers/{customer_id}/statement",
      {
        params: {
          path: { customer_id: customerId },
          query: {
            as_of: asOf,
            from_date: fromDate,
            to_date: toDate,
          },
        },
      },
    );
    if (result.data !== undefined) {
      return Response.json(result.data, {
        headers: { "X-Correlation-ID": correlationId },
        status: result.response.status,
      });
    }
    const envelope = (result.error ?? {}) as {
      error?: { code?: string; correlation_id?: string; message?: string };
    };
    return Response.json(
      {
        code: envelope.error?.code ?? "statement_unavailable",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Statement was not returned.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "statement_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Statement service unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}

function failureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 400 || status === 422) return "validation";
  return "unavailable";
}
