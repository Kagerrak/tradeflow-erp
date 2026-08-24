import { getServerApiConfig } from "@/lib/server-api";
import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export const dynamic = "force-dynamic";

function failureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "validation";
  return "unavailable";
}

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "not_found") return 404;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

type ConvertPurchaseRequestInput =
  components["schemas"]["ConvertPurchaseRequestCommand"];

export async function POST(
  request: Request,
  context: { params: Promise<{ purchaseRequestId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { purchaseRequestId } = await context.params;
  const accessToken = getServerApiConfig().accessToken;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
      },
      { status: 401 },
    );
  }

  const command = (await request.json()) as ConvertPurchaseRequestInput;
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
  });

  try {
    const result = await client.POST(
      "/v1/procurement/purchase-requests/{purchase_request_id}/conversions",
      {
        body: command,
        params: { path: { purchase_request_id: purchaseRequestId } },
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
        code: envelope.error?.code ?? "purchase_request_not_converted",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message:
          envelope.error?.message ?? "Purchase request was not converted.",
      },
      { status: statusFor(failureKind(result.response.status)) },
    );
  } catch {
    return Response.json(
      {
        code: "purchase_request_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Purchase request service unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}
