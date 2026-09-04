export type TelemetryContext = {
  correlationId: string;
  service: "tradeflow-web" | "tradeflow-mobile";
};

export function createTelemetryContext(
  service: TelemetryContext["service"],
  createCorrelationId: () => string = randomCorrelationId,
): TelemetryContext {
  return {
    correlationId: createCorrelationId(),
    service,
  };
}

/**
 * crypto.randomUUID() only exists in secure contexts (HTTPS or localhost);
 * assemble a v4 UUID from getRandomValues() so plain-HTTP deployments work.
 */
function randomCorrelationId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi !== undefined && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}
