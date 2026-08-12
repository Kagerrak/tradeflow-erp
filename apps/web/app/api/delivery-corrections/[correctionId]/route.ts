import {
  correctionUnavailableResponse,
  createCorrectionClient,
  normalizeCorrectionError,
  unauthenticatedResponse,
} from "../../../../lib/correction-api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ correctionId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createCorrectionClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before opening this Delivery Correction.",
    );
  }
  const { correctionId } = await context.params;
  try {
    const result = await client.GET(
      "/v1/delivery-corrections/{correction_id}",
      { params: { path: { correction_id: correctionId } } },
    );
    return normalizeCorrectionError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage: "The Delivery Correction could not be reached.",
      },
    );
  } catch {
    return correctionUnavailableResponse(
      correlationId,
      "The Delivery Correction could not be reached.",
    );
  }
}
