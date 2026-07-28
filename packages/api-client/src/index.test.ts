import { describe, expect, it, vi } from "vitest";

import { createTradeFlowClient } from "./index";

describe("generated TradeFlow client", () => {
  it("sends bearer and correlation headers through the session contract", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          database: "ready",
          service: "tradeflow-api",
          user: {
            capabilities: ["platform:read"],
            display_name: "Platform Tester",
            subject: "user-123",
          },
        }),
        {
          headers: { "content-type": "application/json" },
          status: 200,
        },
      ),
    );
    const client = createTradeFlowClient({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "d9428888-122b-11e1-b85c-61cd3cbb3210",
      fetch,
    });

    const result = await client.GET("/v1/session");

    expect(result.data?.database).toBe("ready");
    expect(fetch).toHaveBeenCalledOnce();
    const request = fetch.mock.calls[0]?.[0] as Request;
    expect(request.headers.get("authorization")).toBe("Bearer signed-token");
    expect(request.headers.get("x-correlation-id")).toBe(
      "d9428888-122b-11e1-b85c-61cd3cbb3210",
    );
    expect(request.headers.get("traceparent")).toBe(
      "00-d9428888122b11e1b85c61cd3cbb3210-d9428888122b11e1-01",
    );
  });
});
