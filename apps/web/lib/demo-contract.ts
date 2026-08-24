export const DEMO_SEED_VERSION = "2026.08.24.1";
export const DEMO_RESET_MINUTES = 45;

export const demoRecords = {
  approvedOrder: "20000000-0000-4000-8000-000000000103",
  customer: "20000000-0000-4000-8000-000000000201",
  delivery: "20000000-0000-4000-8000-000000000601",
  fulfillmentOrder: "20000000-0000-4000-8000-000000000401",
  invoice: "20000000-0000-4000-8000-000000000701",
  payment: "20000000-0000-4000-8000-000000000801",
} as const;

export function nextDemoReset(now = new Date()): Date {
  const intervalMs = DEMO_RESET_MINUTES * 60_000;
  return new Date(Math.ceil(now.getTime() / intervalMs) * intervalMs);
}
