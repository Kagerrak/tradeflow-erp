import { describe, expect, it } from "vitest";

import { createTelemetryContext } from "./index";

describe("telemetry context", () => {
  it("creates a client correlation identity with the selected service", () => {
    const context = createTelemetryContext("tradeflow-web");

    expect(context.service).toBe("tradeflow-web");
    expect(context.correlationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u,
    );
  });

  it("uses an injected correlation identity generator on native clients", () => {
    const context = createTelemetryContext(
      "tradeflow-mobile",
      () => "mobile-correlation-id",
    );

    expect(context).toEqual({
      correlationId: "mobile-correlation-id",
      service: "tradeflow-mobile",
    });
  });
});
