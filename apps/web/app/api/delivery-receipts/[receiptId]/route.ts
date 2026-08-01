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
  return Response.json(result.data ?? result.error, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: result.response.status,
  });
}
