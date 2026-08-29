import {
  createBusinessClient,
  normalizeBusinessError,
  serviceUnavailableResponse,
  unauthenticatedResponse,
} from "../../../../../../../lib/correction-api";

export async function POST(
  _request: Request,
  context: {
    params: Promise<{ requestId: string; evidenceId: string }>;
  },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createBusinessClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before completing Return evidence upload.",
    );
  }
  const { requestId, evidenceId } = await context.params;
  try {
    const result = await client.POST(
      "/v1/return-requests/{return_request_id}/evidence/{evidence_id}/complete",
      {
        params: {
          path: { return_request_id: requestId, evidence_id: evidenceId },
        },
      },
    );
    return normalizeBusinessError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage: "Return evidence could not be verified.",
      },
    );
  } catch {
    return serviceUnavailableResponse(
      "return_evidence_service_unavailable",
      correlationId,
      "Return evidence could not be verified.",
    );
  }
}
