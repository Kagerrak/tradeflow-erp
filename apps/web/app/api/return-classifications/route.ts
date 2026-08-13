import {
  createBusinessClient,
  normalizeBusinessError,
  serviceUnavailableResponse,
  unauthenticatedResponse,
} from "../../../lib/correction-api";

export async function GET(): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createBusinessClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before creating a Return Request.",
    );
  }
  try {
    const result = await client.GET("/v1/return-classifications");
    return normalizeBusinessError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      { defaultMessage: "Return classifications could not be reached." },
    );
  } catch {
    return serviceUnavailableResponse(
      "return_classification_service_unavailable",
      correlationId,
      "Return classifications could not be reached.",
    );
  }
}
