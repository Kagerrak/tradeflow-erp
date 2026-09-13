import "server-only";

/**
 * Demo lifecycle state, read from the API's coordination endpoint.
 *
 * The API answers this path purely from DynamoDB, so a status poll can never
 * wake a paused Aurora cluster. The page tier keeps no AWS credentials and no
 * AWS API permissions; it asks the API, exactly like every other read.
 *
 * Reads use a short-lived cache. Expired reads await their refresh because
 * Lambda freezes background work after returning a response. A cold API can
 * take several seconds to initialize, even though this endpoint uses no SQL.
 */

export type DemoLifecycle = "ready" | "refreshing" | "failed" | "unknown";

export type DemoState = Readonly<{
  status: DemoLifecycle;
  seedVersion: string | null;
  nextResetAt: string | null;
  resetStartedAt: string | null;
  lastError: string | null;
  records: Record<string, unknown> | undefined;
}>;

const CACHE_MS = 5_000;
const PROBE_TIMEOUT_MS = 12_000;

let cached: { at: number; value: DemoState } | undefined;
let inFlight: Promise<DemoState> | undefined;

const unknownState: DemoState = {
  status: "unknown",
  seedVersion: null,
  nextResetAt: null,
  resetStartedAt: null,
  lastError: null,
  records: undefined,
};

export function invalidateDemoState(): void {
  cached = undefined;
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function toState(payload: Record<string, unknown>): DemoState {
  const status = asText(payload.status);
  const manifest = payload.manifest;
  const records =
    manifest && typeof manifest === "object"
      ? (manifest as { records?: Record<string, unknown> }).records
      : undefined;
  return {
    status:
      status === "ready" || status === "refreshing" || status === "failed"
        ? status
        : "unknown",
    seedVersion: asText(payload.seedVersion),
    nextResetAt: asText(payload.nextResetAt),
    resetStartedAt: asText(payload.resetStartedAt),
    lastError: asText(payload.lastError),
    records,
  };
}

async function probe(): Promise<DemoState> {
  const baseUrl = process.env.TRADEFLOW_API_URL;
  if (!baseUrl) return cached?.value ?? unknownState;
  try {
    const response = await fetch(new URL("/v1/demo/state", baseUrl), {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    if (!response.ok) {
      return { ...(cached?.value ?? unknownState), status: "unknown" };
    }
    return toState((await response.json()) as Record<string, unknown>);
  } catch {
    // Unreachable API. Report unknown rather than pretending the demo is
    // refreshing: the API itself still refuses traffic while it is not ready.
    return { ...(cached?.value ?? unknownState), status: "unknown" };
  }
}

function refresh(): Promise<DemoState> {
  if (inFlight) return inFlight;
  inFlight = probe()
    .then((value) => {
      cached = { at: Date.now(), value };
      return value;
    })
    .finally(() => {
      inFlight = undefined;
    });
  return inFlight;
}

export async function readDemoState(): Promise<DemoState> {
  const now = Date.now();
  if (cached) {
    if (now - cached.at < CACHE_MS) return cached.value;
    // Lambda can freeze timers and I/O after the response. Complete the probe
    // during this invocation so a cold-start "unknown" cannot stay cached.
    return refresh();
  }
  return refresh();
}

/** True when the console should show "preparing" or a genuine failure. */
export function demoIsBlocked(state: DemoState): boolean {
  return state.status === "refreshing" || state.status === "failed";
}
