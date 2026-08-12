import {
  type PaymentVerificationInput,
  verifyPaymentReceipt,
} from "@tradeflow/payment-clearance";

type VerificationBody = {
  command: PaymentVerificationInput;
  idempotencyKey: string;
};

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

export async function POST(
  request: Request,
  context: { params: Promise<{ paymentReceiptId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { paymentReceiptId } = await context.params;
  const body = (await request.json()) as VerificationBody;
  const state = await verifyPaymentReceipt({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    command: body.command,
    correlationId,
    idempotencyKey: body.idempotencyKey,
    paymentReceiptId,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
