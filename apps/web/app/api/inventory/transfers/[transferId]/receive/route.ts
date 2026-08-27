import { getServerApiConfig } from "@/lib/server-api";
import { createTradeFlowClient } from "@tradeflow/api-client";

import { transferFailureKind } from "../../response";

const baseUrl = getServerApiConfig().baseUrl;

type Body = {
  expectedVersion: number;
  idempotencyKey: string;
};

export async function POST(
  request: Request,
  context: { params: Promise<{ transferId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { transferId } = await context.params;
  const body = (await request.json()) as Body;
  const accessToken = getServerApiConfig().accessToken;
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
        body: { expected_version: body.expectedVersion },
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
        kind: transferFailureKind(result.response.status),
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
