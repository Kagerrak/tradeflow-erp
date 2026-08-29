import { type components } from "@tradeflow/api-client";

import {
  createBusinessClient,
  normalizeBusinessError,
  serviceUnavailableResponse,
  unauthenticatedResponse,
} from "../../../../../../lib/correction-api";

type Command = components["schemas"]["EvidenceUploadIntent"];

export async function POST(
  request: Request,
  context: { params: Promise<{ requestId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createBusinessClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before uploading Return evidence.",
    );
  }
  const { requestId } = await context.params;
  const input = (await request.json()) as { command: Command };
  try {
    const result = await client.POST(
      "/v1/return-requests/{return_request_id}/evidence/uploads",
      {
        body: input.command,
        params: { path: { return_request_id: requestId } },
      },
    );
    return normalizeBusinessError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage: "Return evidence upload could not be prepared.",
      },
    );
  } catch {
    return serviceUnavailableResponse(
      "return_evidence_service_unavailable",
      correlationId,
      "Return evidence upload could not be prepared.",
    );
  }
}
