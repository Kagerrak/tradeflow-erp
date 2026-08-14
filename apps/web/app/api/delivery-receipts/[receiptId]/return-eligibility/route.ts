import {
  createBusinessClient,
  normalizeBusinessError,
  serviceUnavailableResponse,
  unauthenticatedResponse,
} from "../../../../../lib/correction-api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ receiptId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createBusinessClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before checking return eligibility.",
    );
  }
  const { receiptId } = await context.params;
  try {
    const result = await client.GET(
      "/v1/delivery-receipts/{receipt_id}/return-eligibility",
      { params: { path: { receipt_id: receiptId } } },
    );
    return normalizeBusinessError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      { defaultMessage: "Return eligibility could not be loaded." },
    );
  } catch {
    return serviceUnavailableResponse(
      "return_eligibility_service_unavailable",
      correlationId,
      "Return eligibility could not be loaded.",
    );
  }
}
