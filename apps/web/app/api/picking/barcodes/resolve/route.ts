import { resolveBarcode } from "@tradeflow/warehouse-picking";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "scan_denied" || kind === "validation") return 422;
  if (kind === "conflict") return 409;
  if (kind === "not_found") return 404;
  if (kind === "unavailable") return 503;
  return 200;
}

type ResolveBody = {
  barcode: string;
  fulfillmentOrderId: string;
  lineId: string;
  warehouseId: string;
};

export async function POST(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const body = (await request.json()) as ResolveBody;
  const state = await resolveBarcode({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    barcode: body.barcode,
    baseUrl,
    correlationId,
    fulfillmentOrderId: body.fulfillmentOrderId,
    lineId: body.lineId,
    warehouseId: body.warehouseId,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
