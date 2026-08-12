import { type operations } from "@tradeflow/api-client";

import {
  correctionUnavailableResponse,
  createCorrectionClient,
  normalizeCorrectionError,
  unauthenticatedResponse,
} from "../../../lib/correction-api";

type CorrectionListQuery =
  operations["list_delivery_corrections_v1_delivery_corrections_get"]["parameters"]["query"];

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createCorrectionClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before reviewing Delivery Corrections.",
    );
  }
  const status = new URL(request.url).searchParams.get("status");
  const query: CorrectionListQuery =
    status === null
      ? {}
      : { status: status as "pending_authorization" | "posted" };
  try {
    const result = await client.GET("/v1/delivery-corrections", {
      params: { query },
    });
    return normalizeCorrectionError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultCode: "delivery_correction_service_unavailable",
        defaultMessage: "Delivery Corrections could not be reached.",
      },
    );
  } catch {
    return correctionUnavailableResponse(
      correlationId,
      "Delivery Corrections could not be reached.",
    );
  }
}
