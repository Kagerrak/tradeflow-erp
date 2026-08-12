import { createTradeFlowClient, type components } from "@tradeflow/api-client";

type EvidenceIntent = components["schemas"]["EvidenceUploadIntent"];
type ConfirmationCommand = components["schemas"]["ConfirmDeliveryCommand"];

type Action =
  | { action: "complete"; evidenceId: string }
  | { action: "confirm"; command: ConfirmationCommand; idempotencyKey: string }
  | { action: "intent"; command: EvidenceIntent };

export async function POST(
  request: Request,
  context: { params: Promise<{ deliveryId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { deliveryId } = await context.params;
  const action = (await request.json()) as Action;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return stateResponse(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before confirming a Delivery.",
      },
      401,
      correlationId,
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId,
  });
  try {
    const result =
      action.action === "intent"
        ? await client.POST("/v1/deliveries/{delivery_id}/evidence/uploads", {
            body: action.command,
            params: { path: { delivery_id: deliveryId } },
          })
        : action.action === "complete"
          ? await client.POST(
              "/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
              {
                params: {
                  path: {
                    delivery_id: deliveryId,
                    evidence_id: action.evidenceId,
                  },
                },
              },
            )
          : await client.POST("/v1/deliveries/{delivery_id}/confirmations", {
              body: action.command,
              headers: { "Idempotency-Key": action.idempotencyKey },
              params: { path: { delivery_id: deliveryId } },
            });
    if (result.data !== undefined) {
      return stateResponse(result.data, result.response.status, correlationId);
    }
    const envelope = (result.error ?? {}) as {
      error?: { code?: string; correlation_id?: string; message?: string };
    };
    return stateResponse(
      {
        code:
          envelope.error?.code ?? `http_${result.response.status.toString()}`,
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message:
          envelope.error?.message ?? "The Delivery request was not accepted.",
      },
      result.response.status,
      correlationId,
    );
  } catch {
    return stateResponse(
      {
        code: "delivery_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "The Delivery outcome is uncertain. Retry unchanged work.",
      },
      503,
      correlationId,
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

function stateResponse(
  value: object,
  status: number,
  correlationId: string,
): Response {
  return Response.json(value, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status,
  });
}
