import { type components, type operations } from "@tradeflow/api-client";

import {
  createBusinessClient,
  normalizeBusinessError,
  serviceUnavailableResponse,
  unauthenticatedResponse,
} from "../../../lib/correction-api";

type Query =
  operations["list_return_requests_v1_return_requests_get"]["parameters"]["query"];
type CreateCommand = components["schemas"]["CreateReturnRequest"];

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createBusinessClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before reviewing Return Requests.",
    );
  }
  const status = new URL(request.url).searchParams.get("status");
  const query: Query =
    status === null
      ? {}
      : { status: status as "pending_authorization" | "authorized" };
  try {
    const result = await client.GET("/v1/return-requests", {
      params: { query },
    });
    return normalizeBusinessError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage: "Return Requests could not be reached.",
      },
    );
  } catch {
    return serviceUnavailableResponse(
      "return_request_service_unavailable",
      correlationId,
      "Return Requests could not be reached.",
    );
  }
}

export async function POST(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createBusinessClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before creating a Return Request.",
    );
  }
  const input = (await request.json()) as {
    command: CreateCommand;
    idempotencyKey: string;
    receiptId: string;
  };
  try {
    const result = await client.POST(
      "/v1/delivery-receipts/{receipt_id}/return-requests",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
        params: { path: { receipt_id: input.receiptId } },
      },
    );
    return normalizeBusinessError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage:
          "The Return Request outcome is uncertain. Retry unchanged work.",
      },
    );
  } catch {
    return serviceUnavailableResponse(
      "return_request_service_unavailable",
      correlationId,
      "The Return Request outcome is uncertain. Retry unchanged work.",
    );
  }
}
