export type TelemetryContext = {
  correlationId: string;
  service: "tradeflow-web" | "tradeflow-mobile";
};

export function createTelemetryContext(
  service: TelemetryContext["service"],
  createCorrelationId: () => string = () => globalThis.crypto.randomUUID(),
): TelemetryContext {
  return {
    correlationId: createCorrelationId(),
    service,
  };
}
