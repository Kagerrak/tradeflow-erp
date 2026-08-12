import { type components } from "@tradeflow/api-client";

import {
  correctionUnavailableResponse,
  createCorrectionClient,
  normalizeCorrectionError,
  unauthenticatedResponse,
} from "../../../../../lib/correction-api";

type AuthorizeCommand = components["schemas"]["AuthorizeDeliveryCorrection"];

export async function POST(
  request: Request,
  context: { params: Promise<{ correctionId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createCorrectionClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before authorizing a Delivery Correction.",
    );
  }
  const { correctionId } = await context.params;
  const input = (await request.json()) as {
    command: AuthorizeCommand;
    idempotencyKey: string;
  };
  try {
    const result = await client.POST(
      "/v1/delivery-corrections/{correction_id}/authorization",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
        params: { path: { correction_id: correctionId } },
      },
    );
    return normalizeCorrectionError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage:
          "The authorization outcome is uncertain. Retry unchanged work.",
      },
    );
  } catch {
    return correctionUnavailableResponse(
      correlationId,
      "The authorization outcome is uncertain. Retry unchanged work.",
    );
  }
}
