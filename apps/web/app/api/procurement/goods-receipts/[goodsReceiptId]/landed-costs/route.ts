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

type CreateLandedCostInput = components["schemas"]["CreateLandedCostCommand"];

export async function GET(
  request: Request,
  context: { params: Promise<{ goodsReceiptId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { goodsReceiptId } = await context.params;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
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

  const client = createTradeFlowClient({
    accessToken,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId,
  });

  try {
    const result = await client.GET(
      "/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
      {
        params: { path: { goods_receipt_id: goodsReceiptId } },
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
        code: envelope.error?.code ?? "landed_costs_unavailable",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Landed costs were not returned.",
      },
      { status: statusFor(failureKind(result.response.status)) },
    );
  } catch {
    return Response.json(
      {
        code: "landed_cost_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Landed cost service unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}

export async function POST(
  request: Request,
  context: { params: Promise<{ goodsReceiptId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { goodsReceiptId } = await context.params;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
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

  const command = (await request.json()) as CreateLandedCostInput;
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId,
  });

  try {
    const result = await client.POST(
      "/v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs",
      {
        body: command,
        params: { path: { goods_receipt_id: goodsReceiptId } },
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
        code: envelope.error?.code ?? "landed_cost_not_allocated",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Landed cost was not allocated.",
      },
      { status: statusFor(failureKind(result.response.status)) },
    );
  } catch {
    return Response.json(
      {
        code: "landed_cost_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Landed cost service unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}
