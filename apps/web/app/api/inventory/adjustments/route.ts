import { createTradeFlowClient } from "@tradeflow/api-client";

import { adjustmentFailureKind } from "./response";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

type RequestBody = {
  skuId: string;
  warehouseId: string;
  locationId: string;
  kind: "surplus" | "shortage";
  quantity: string;
  unitCode: string;
  reason: string;
  sourceReference: string;
  lotCode?: string;
  idempotencyKey: string;
};

export async function POST(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const body = (await request.json()) as RequestBody;
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before requesting an adjustment.",
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
    const result = await client.POST("/v1/inventory/adjustments", {
      body: {
        sku_id: body.skuId,
        warehouse_id: body.warehouseId,
        location_id: body.locationId,
        kind: body.kind,
        quantity: body.quantity,
        unit_code: body.unitCode,
        reason: body.reason,
        source_reference: body.sourceReference,
        lot_code: body.lotCode ?? null,
      },
      headers: { "Idempotency-Key": body.idempotencyKey },
    });
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
        code: envelope.error?.code ?? "adjustment_request_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: adjustmentFailureKind(result.response.status),
        message:
          envelope.error?.message ?? "Adjustment request was not accepted.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "adjustment_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Adjustment service is unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before viewing adjustments.",
      },
      { status: 401 },
    );
  }
  const client = createTradeFlowClient({
    accessToken,
    baseUrl,
    correlationId,
  });
  const { searchParams } = new URL(request.url);
  try {
    const result = await client.GET("/v1/inventory/adjustments", {
      params: {
        query: {
          limit: Number(searchParams.get("limit") ?? "50"),
          offset: Number(searchParams.get("offset") ?? "0"),
        },
      },
    });
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
        code: envelope.error?.code ?? "adjustment_list_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: adjustmentFailureKind(result.response.status),
        message: envelope.error?.message ?? "Adjustment list is unavailable.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "adjustment_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Adjustment service is unavailable; retry later.",
      },
      { status: 503 },
    );
  }
}
