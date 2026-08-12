import { describe, expect, it } from "vitest";

import { loadPlatformSession } from "./index";

describe("platform session journey", () => {
  it("asks the operator to sign in when no access token is available", async () => {
    const state = await loadPlatformSession({
      accessToken: undefined,
      baseUrl: "https://api.tradeflow.test",
      correlationId: "03f9ef54-b676-4efd-b0af-a6ce823c3aae",
    });

    expect(state).toEqual({
      correlationId: "03f9ef54-b676-4efd-b0af-a6ce823c3aae",
      kind: "unauthenticated",
    });
  });

  it("returns the authoritative operator session from TradeFlow", async () => {
    const state = await loadPlatformSession({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "6ab7c99a-5ad3-4635-a929-cfb476c6f51d",
      fetch: () =>
        Promise.resolve(
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
              headers: {
                "content-type": "application/json",
                "x-correlation-id": "6ab7c99a-5ad3-4635-a929-cfb476c6f51d",
              },
              status: 200,
            },
          ),
        ),
    });

    expect(state).toEqual({
      correlationId: "6ab7c99a-5ad3-4635-a929-cfb476c6f51d",
      database: "ready",
      kind: "ready",
      service: "tradeflow-api",
      user: {
        capabilities: ["platform:read"],
        displayName: "Platform Tester",
        subject: "user-123",
      },
    });
  });

  it("reports when the signed-in operator lacks platform access", async () => {
    const state = await loadPlatformSession({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "26dfdf12-fea1-44f8-b55a-3226c816cbee",
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "capability_required",
                correlation_id: "26dfdf12-fea1-44f8-b55a-3226c816cbee",
                message: "The 'platform:read' capability is required.",
              },
            }),
            {
              headers: { "content-type": "application/json" },
              status: 403,
            },
          ),
        ),
    });

    expect(state).toEqual({
      correlationId: "26dfdf12-fea1-44f8-b55a-3226c816cbee",
      kind: "forbidden",
    });
  });

  it("returns to sign-in when the identity session has expired", async () => {
    const state = await loadPlatformSession({
      accessToken: "expired-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "cc8dd581-c712-4b2e-8106-c5776e26f0af",
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "invalid_token",
                correlation_id: "cc8dd581-c712-4b2e-8106-c5776e26f0af",
                message: "The bearer token is invalid or expired.",
              },
            }),
            {
              headers: { "content-type": "application/json" },
              status: 401,
            },
          ),
        ),
    });

    expect(state).toEqual({
      correlationId: "cc8dd581-c712-4b2e-8106-c5776e26f0af",
      kind: "unauthenticated",
    });
  });

  it("keeps the operator informed when TradeFlow cannot be reached", async () => {
    const state = await loadPlatformSession({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "c16455f7-8c32-42d6-807c-ea72480d2785",
      fetch: () => Promise.reject(new TypeError("Network request failed")),
    });

    expect(state).toEqual({
      correlationId: "c16455f7-8c32-42d6-807c-ea72480d2785",
      kind: "unavailable",
    });
  });

  it("reports temporary unavailability when the platform is not ready", async () => {
    const state = await loadPlatformSession({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "28c30f56-0af0-4267-a118-71f2f6b8b643",
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "service_unavailable",
                correlation_id: "28c30f56-0af0-4267-a118-71f2f6b8b643",
                message: "TradeFlow is temporarily unavailable.",
              },
            }),
            {
              headers: { "content-type": "application/json" },
              status: 503,
            },
          ),
        ),
    });

    expect(state).toEqual({
      correlationId: "28c30f56-0af0-4267-a118-71f2f6b8b643",
      kind: "unavailable",
    });
  });
});
