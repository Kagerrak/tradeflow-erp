import { type components } from "@tradeflow/api-client";

import {
  correctionUnavailableResponse,
  createCorrectionClient,
  normalizeCorrectionError,
  unauthenticatedResponse,
} from "../../../../../lib/correction-api";

type RequestCommand = components["schemas"]["RequestDeliveryCorrection"];

export async function POST(
  request: Request,
  context: { params: Promise<{ receiptId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createCorrectionClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before requesting a Delivery Correction.",
    );
  }
  const { receiptId } = await context.params;
  const input = (await request.json()) as {
    command: RequestCommand;
    idempotencyKey: string;
  };
  try {
    const result = await client.POST(
      "/v1/delivery-receipts/{receipt_id}/corrections",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
        params: { path: { receipt_id: receiptId } },
      },
    );
    return normalizeCorrectionError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage:
          "The correction request outcome is uncertain. Retry unchanged work.",
      },
    );
  } catch {
    return correctionUnavailableResponse(
      correlationId,
      "The correction request outcome is uncertain. Retry unchanged work.",
    );
  }
}
