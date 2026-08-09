export async function POST(
  request: Request,
  context: { params: Promise<{ deliveryId: string }> },
): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const token = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (token === undefined || token === "")
    return state(401, correlationId, null);
  const { deliveryId } = await context.params;
  const input = (await request.json()) as {
    command: object;
    idempotencyKey: string;
  };
  try {
    const response = await fetch(
      `${process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000"}/v1/deliveries/${deliveryId}/return-to-warehouse-receipts`,
      {
        body: JSON.stringify(input.command),
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "Idempotency-Key": input.idempotencyKey,
          "X-Correlation-ID": correlationId,
        },
        method: "POST",
      },
    );
    return state(
      response.status,
      correlationId,
      (await response.json()) as object,
    );
  } catch {
    return state(503, correlationId, null);
  }
}

function state(status: number, correlationId: string, payload: object | null) {
  if (status >= 200 && status < 300 && payload !== null)
    return Response.json(payload, { status });
  const envelope = (payload ?? {}) as {
    error?: { code?: string; correlation_id?: string; message?: string };
  };
  const kind =
    status === 401
      ? "unauthenticated"
      : status === 403
        ? "forbidden"
        : status === 409
          ? "conflict"
          : status === 400 || status === 422
            ? "validation"
            : "unavailable";
  return Response.json(
    {
      code: envelope.error?.code ?? `http_${status}`,
      correlationId: envelope.error?.correlation_id ?? correlationId,
      kind,
      message:
        envelope.error?.message ?? "The return receipt outcome is uncertain.",
    },
    { status },
  );
}
