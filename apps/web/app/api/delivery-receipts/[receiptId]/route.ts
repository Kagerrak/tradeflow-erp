import { createTradeFlowClient } from "@tradeflow/api-client";

export async function GET(
  _request: Request,
  context: { params: Promise<{ receiptId: string }> },
): Promise<Response> {
  return receiptRequest(context, "detail");
}

export async function POST(
  _request: Request,
  context: { params: Promise<{ receiptId: string }> },
): Promise<Response> {
  return receiptRequest(context, "access");
}

async function receiptRequest(
  context: { params: Promise<{ receiptId: string }> },
  action: "access" | "detail",
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before opening a Delivery Receipt.",
      },
      { status: 401 },
    );
  }
  const { receiptId } = await context.params;
  try {
    const client = createTradeFlowClient({
      accessToken,
      baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
      correlationId,
    });
    const result =
      action === "detail"
        ? await client.GET("/v1/delivery-receipts/{delivery_receipt_id}", {
            params: { path: { delivery_receipt_id: receiptId } },
          })
        : await client.POST(
            "/v1/delivery-receipts/{delivery_receipt_id}/access",
            { params: { path: { delivery_receipt_id: receiptId } } },
          );
    return normalized(
      result.data ?? result.error,
      result.response.status,
      correlationId,
    );
  } catch {
    return normalized(null, 503, correlationId);
  }
}

function normalized(
  payload: object | null | undefined,
  status: number,
  correlationId: string,
): Response {
  if (
    status >= 200 &&
    status < 300 &&
    payload !== null &&
    payload !== undefined
  )
    return Response.json(payload, {
      headers: {
        "Cache-Control": "no-store",
        "X-Correlation-ID": correlationId,
      },
      status,
    });
  const envelope = (payload ?? {}) as {
    error?: { code?: string; correlation_id?: string; message?: string };
  };
  const kind =
    status === 401
      ? "unauthenticated"
      : status === 403
        ? "forbidden"
        : status === 409
          ? "conflict"
          : status === 400 || status === 422
            ? "validation"
            : "unavailable";
  return Response.json(
    {
      code: envelope.error?.code ?? `http_${status}`,
      correlationId: envelope.error?.correlation_id ?? correlationId,
      kind,
      message:
        envelope.error?.message ??
        (status === 401
          ? "Sign in before opening a Delivery Receipt."
          : "The Delivery Receipt could not be reached."),
    },
    {
      headers: {
        "Cache-Control": "no-store",
        "X-Correlation-ID": correlationId,
      },
      status,
    },
  );
}
