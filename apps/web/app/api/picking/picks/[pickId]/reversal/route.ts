import {
  reversePick,
  type ReversePickCommand,
} from "@tradeflow/warehouse-picking";

const baseUrl = process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";

function statusFor(kind: string): number {
  if (kind === "unauthenticated") return 401;
  if (kind === "forbidden") return 403;
  if (kind === "not_found") return 404;
  if (kind === "conflict") return 409;
  if (kind === "validation") return 422;
  if (kind === "unavailable") return 503;
  return 200;
}

type ReverseBody = {
  command: ReversePickCommand;
  idempotencyKey: string;
};

export async function POST(
  request: Request,
  context: { params: Promise<{ pickId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const { pickId } = await context.params;
  const body = (await request.json()) as ReverseBody;
  const state = await reversePick({
    accessToken: process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl,
    command: body.command,
    correlationId,
    idempotencyKey: body.idempotencyKey,
    pickId,
  });
  return Response.json(state, {
    headers: { "Cache-Control": "no-store", "X-Correlation-ID": correlationId },
    status: statusFor(state.kind),
  });
}
