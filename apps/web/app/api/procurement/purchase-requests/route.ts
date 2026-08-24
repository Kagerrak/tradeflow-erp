import { getServerApiConfig } from "@/lib/server-api";
import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export const dynamic = "force-dynamic";

function failureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 422) return "validation";
  return "unavailable";
}

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

type CreatePurchaseRequestInput =
  components["schemas"]["CreatePurchaseRequestCommand"];

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const url = new URL(request.url);
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

  const client = createTradeFlowClient({
    accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
  });

  try {
    const params: Record<string, unknown> = {};
    if (url.searchParams.get("query")) {
      params.query = url.searchParams.get("query");
    }
    if (url.searchParams.get("status")) {
      params.status = url.searchParams.get("status");
    }
    const result = await client.GET("/v1/procurement/purchase-requests", {
      params: { query: params },
    });
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
        code: envelope.error?.code ?? "purchase_requests_unavailable",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message:
          envelope.error?.message ?? "Purchase requests were not returned.",
      },
      { status: result.response.status },
    );
  } catch {
    return Response.json(
      {
        code: "purchase_requests_service_unavailable",
        correlationId,
        kind: "unavailable",
        message: "Purchase request service unavailable; retry unchanged work.",
      },
      { status: 503 },
    );
  }
}

export async function POST(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
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

  const command = (await request.json()) as CreatePurchaseRequestInput;
  const client = createTradeFlowClient({
    accessToken,
    baseUrl: getServerApiConfig().baseUrl,
    correlationId,
  });

  try {
    const result = await client.POST("/v1/procurement/purchase-requests", {
      body: command,
    });
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
        code: envelope.error?.code ?? "purchase_request_not_created",
        correlationId: envelope.error?.correlation_id ?? correlationId,
        kind: failureKind(result.response.status),
        message: envelope.error?.message ?? "Purchase request was not created.",
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
