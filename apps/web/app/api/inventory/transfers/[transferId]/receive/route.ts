import { createTradeFlowClient } from "@tradeflow/api-client";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

type Body = {
  idempotencyKey: string;
};

export async function POST(
  request: Request,
  context: { params: Promise<{ transferId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { transferId } = await context.params;
  const body = (await request.json()) as Body;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before receiving a transfer.",
      },
      { status: 401 },
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl,
    correlationId,
  });
  try {
    const result = await client.POST(
      "/v1/inventory/transfers/{transfer_id}/receive",
      {
        body: {},
        headers: { "Idempotency-Key": body.idempotencyKey },
        params: { path: { transfer_id: transferId } },
      },
    );
    if (result.data !== undefined) {
      return Response.json(result.data, {
        headers: {
          "Cache-Control": "no-store",
          "X-Correlation-ID": correlationId,
        },
        status: result.response.status,
      });
    }
    const envelope = (result.error ?? {}) as {
      error?: { code?: string; correlation_id?: string; message?: string };
    };
    return Response.json(
      {
        code: envelope.error?.code ?? "transfer_receive_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message:
          envelope.error?.message ?? "Transfer receive was not accepted.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "transfer_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Transfer service is unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}

function failureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  return "unavailable";
}
