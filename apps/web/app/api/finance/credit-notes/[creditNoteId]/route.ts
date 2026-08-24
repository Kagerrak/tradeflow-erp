import { getServerApiConfig } from "@/lib/server-api";
import { createTradeFlowClient } from "@tradeflow/api-client";

export async function GET(
  _request: Request,
  context: { params: Promise<{ creditNoteId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { creditNoteId } = await context.params;
  const accessToken = getServerApiConfig().accessToken;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before viewing this credit note.",
      },
      { status: 401 },
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
  });
  try {
    const result = await client.GET(
      "/v1/finance/credit-notes/{credit_note_id}",
      {
        params: { path: { credit_note_id: creditNoteId } },
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
        code: envelope.error?.code ?? "credit_note_detail_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message:
          envelope.error?.message ?? "Credit note detail is unavailable.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "credit_note_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Credit note service is unavailable; retry later.",
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
