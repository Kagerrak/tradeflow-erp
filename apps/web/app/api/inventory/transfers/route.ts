import { createTradeFlowClient } from "@tradeflow/api-client";

import { transferFailureKind } from "./response";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

type RequestBody = {
  skuId: string;
  fromWarehouseId: string;
  toWarehouseId: string;
  fromLocationId: string;
  toLocationId: string;
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
        message: "Sign in before requesting a transfer.",
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
    const result = await client.POST("/v1/inventory/transfers", {
      body: {
        sku_id: body.skuId,
        from_warehouse_id: body.fromWarehouseId,
        to_warehouse_id: body.toWarehouseId,
        from_location_id: body.fromLocationId,
        to_location_id: body.toLocationId,
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
        code: envelope.error?.code ?? "transfer_request_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: transferFailureKind(result.response.status),
        message:
          envelope.error?.message ?? "Transfer request was not accepted.",
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

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return Response.json(
      {
        code: "authentication_required",
        correlationId,
        kind: "unauthenticated",
        message: "Sign in before viewing transfers.",
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
    const result = await client.GET("/v1/inventory/transfers", {
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
        code: envelope.error?.code ?? "transfer_list_rejected",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: transferFailureKind(result.response.status),
        message: envelope.error?.message ?? "Transfer list is unavailable.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "transfer_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Transfer service is unavailable; retry later.",
      },
      { status: 503 },
    );
  }
}
