import {
  listPaymentReceipts,
  type PaymentReceiptStatus,
  recordPaymentReceipt,
  type RecordPaymentReceiptInput,
} from "@tradeflow/payment-clearance";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const url = new URL(request.url);
  const status = url.searchParams.get("status") as PaymentReceiptStatus | null;
  const state = await listPaymentReceipts({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl,
    correlationId,
    ...(url.searchParams.get("branch_id") === null
      ? {}
      : { branchId: url.searchParams.get("branch_id")! }),
    ...(status === null ? {} : { status }),
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}

type RecordBody = {
  command: RecordPaymentReceiptInput;
  idempotencyKey: string;
};

export async function POST(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const body = (await request.json()) as RecordBody;
  const state = await recordPaymentReceipt({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl,
    command: body.command,
    correlationId,
    idempotencyKey: body.idempotencyKey,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
