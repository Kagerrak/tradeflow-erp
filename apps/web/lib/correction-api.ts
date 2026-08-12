import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type CorrectionList = components["schemas"]["DeliveryCorrectionList"];
export type CorrectionResponse =
  components["schemas"]["DeliveryCorrectionResponse"];

export function createCorrectionClient(correlationId: string) {
  const accessToken = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (accessToken === undefined || accessToken.length === 0) {
    return null;
  }
  return createTradeFlowClient({
    accessToken,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    correlationId,
  });
}

export function unauthenticatedResponse(
  correlationId: string,
  message: string,
): Response {
  return correctionResponse(
    {
      code: "authentication_required",
      correlationId,
      kind: "unauthenticated",
      message,
    },
    401,
    correlationId,
  );
}

export function correctionResponse(
  value: object,
  status: number,
  correlationId: string,
): Response {
  return Response.json(value, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status,
  });
}

export function normalizeCorrectionError(
  payload: object | null | undefined,
  status: number,
  correlationId: string,
  options: { defaultCode?: string; defaultMessage: string },
): Response {
  if (
    status >= 200 &&
    status < 300 &&
    payload !== null &&
    payload !== undefined
  ) {
    return correctionResponse(payload, status, correlationId);
  }
  const envelope = (payload ?? {}) as {
    error?: { code?: string; correlation_id?: string; message?: string };
  };
  return correctionResponse(
    {
      code:
        envelope.error?.code ??
        options.defaultCode ??
        (status === 401
          ? "authentication_required"
          : `http_${status.toString()}`),
      correlationId: envelope.error?.correlation_id ?? correlationId,
      kind: correctionFailureKind(status),
      message: envelope.error?.message ?? options.defaultMessage,
    },
    status,
    correlationId,
  );
}

export function correctionFailureKind(status: number): string {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  return "unavailable";
}

export function correctionUnavailableResponse(
  correlationId: string,
  message: string,
): Response {
  return correctionResponse(
    {
      code: "delivery_correction_service_unavailable",
      correlationId,
      kind: "unavailable",
      message,
    },
    503,
    correlationId,
  );
}
