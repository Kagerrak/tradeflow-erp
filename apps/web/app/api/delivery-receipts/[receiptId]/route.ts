import {
  correctionUnavailableResponse,
  createCorrectionClient,
  normalizeCorrectionError,
  unauthenticatedResponse,
} from "../../../../lib/correction-api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ receiptId: string }> },
): Promise<Response> {
  return receiptRequest(context, "detail");
}

export async function POST(
  _request: Request,
  context: { params: Promise<{ receiptId: string }> },
): Promise<Response> {
  return receiptRequest(context, "access");
}

async function receiptRequest(
  context: { params: Promise<{ receiptId: string }> },
  action: "access" | "detail",
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const client = createCorrectionClient(correlationId);
  if (client === null) {
    return unauthenticatedResponse(
      correlationId,
      "Sign in before opening a Delivery Receipt.",
    );
  }
  const { receiptId } = await context.params;
  try {
    const result =
      action === "detail"
        ? await client.GET("/v1/delivery-receipts/{delivery_receipt_id}", {
            params: { path: { delivery_receipt_id: receiptId } },
          })
        : await client.POST(
            "/v1/delivery-receipts/{delivery_receipt_id}/access",
            { params: { path: { delivery_receipt_id: receiptId } } },
          );
    return normalizeCorrectionError(
      result.data ?? result.error,
      result.response.status,
      correlationId,
      {
        defaultMessage: "The Delivery Receipt could not be reached.",
      },
    );
  } catch {
    return correctionUnavailableResponse(
      correlationId,
      "The Delivery Receipt could not be reached.",
    );
  }
}
