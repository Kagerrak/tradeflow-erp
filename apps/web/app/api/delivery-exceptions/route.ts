function failureKind(status: number) {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  return "unavailable";
}

export async function GET(request: Request): Promise<Response> {
  const correlationId = crypto.randomUUID();
  const token = process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;
  if (token === undefined || token === "")
    return normalized(null, 401, correlationId);
  const queue =
    new URL(request.url).searchParams.get("queue") ?? "return_pending";
  try {
    const response = await fetch(
      `${process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000"}/v1/delivery-exceptions?queue=${encodeURIComponent(queue)}`,
      {
        cache: "no-store",
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Correlation-ID": correlationId,
        },
      },
    );
    const payload = (await response.json()) as object;
    return normalized(payload, response.status, correlationId);
  } catch {
    return normalized(null, 503, correlationId);
  }
}

function normalized(
  payload: object | null,
  status: number,
  correlationId: string,
) {
  if (status >= 200 && status < 300 && payload !== null)
    return Response.json(payload, {
      headers: {
        "Cache-Control": "no-store",
        "X-Correlation-ID": correlationId,
      },
      status,
    });
  const envelope = (payload ?? {}) as {
    error?: { code?: string; correlation_id?: string; message?: string };
  };
  return Response.json(
    {
      code:
        envelope.error?.code ??
        (status === 401
          ? "authentication_required"
          : "delivery_exception_service_unavailable"),
      correlationId: envelope.error?.correlation_id ?? correlationId,
      kind: failureKind(status),
      message:
        envelope.error?.message ??
        (status === 401
          ? "Sign in before reviewing exception custody."
          : "Exception custody could not be reached."),
    },
    {
      headers: {
        "Cache-Control": "no-store",
        "X-Correlation-ID": correlationId,
      },
      status,
    },
  );
}
