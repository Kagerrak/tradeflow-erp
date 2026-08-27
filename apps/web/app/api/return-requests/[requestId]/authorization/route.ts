import { type components } from "@tradeflow/api-client";

import {
  createBusinessClient,
  normalizeBusinessError,
  serviceUnavailableResponse,
  unauthenticatedResponse,
} from "../../../../../lib/correction-api";

type Command = components["schemas"]["AuthorizeReturnRequest"];

export async function POST(
  request: Request,
  context: { params: Promise<{ requestId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createBusinessClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before authorizing a Return Request.",
    );
  }
  const { requestId } = await context.params;
  const input = (await request.json()) as {
    command: Command;
    idempotencyKey: string;
  };
  try {
    const result = await client.POST(
      "/v1/return-requests/{return_request_id}/authorization",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
        params: { path: { return_request_id: requestId } },
      },
    );
    return normalizeBusinessError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage:
          "The authorization outcome is uncertain. Retry unchanged work.",
      },
    );
  } catch {
    return serviceUnavailableResponse(
      "return_authorization_service_unavailable",
      correlationId,
      "The authorization outcome is uncertain. Retry unchanged work.",
    );
  }
}
