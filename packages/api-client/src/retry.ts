/**
 * Bounded retry for calls to the TradeFlow API.
 *
 * A paused Aurora Serverless v2 cluster takes roughly 15 seconds to resume, and
 * an idle demo hits that resume path on the first request. Without retries the
 * visitor sees an error where a short wait would have succeeded.
 *
 * The policy is deliberately conservative:
 *
 * - the per-attempt timeout stays below the API Gateway 29-second integration
 *   timeout, so a slow resume surfaces as a timeout we can retry rather than a
 *   gateway error we cannot distinguish from a real failure;
 * - only ``GET``/``HEAD``/``OPTIONS`` or requests carrying an ``Idempotency-Key``
 *   are retried, so a command is never applied twice;
 * - retries stop after a bounded number of attempts with short backoff.
 */

const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);
const DEFAULT_ATTEMPTS = 3;
const DEFAULT_TIMEOUT_MS = 20_000;
const BACKOFF_MS = [400, 1_200];

export type RetryingFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

function backoffFor(attempt: number): number {
  return BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] ?? 1_000;
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function timeoutSignal(milliseconds: number): AbortSignal | undefined {
  if (typeof AbortSignal === "undefined") return undefined;
  if (typeof AbortSignal.timeout === "function") {
    return AbortSignal.timeout(milliseconds);
  }
  const controller = new AbortController();
  setTimeout(() => controller.abort(), milliseconds);
  return controller.signal;
}

function combineSignals(
  first: AbortSignal | undefined,
  second: AbortSignal | undefined,
): AbortSignal | undefined {
  if (!first) return second;
  if (!second) return first;
  if (typeof AbortSignal.any === "function")
    return AbortSignal.any([first, second]);
  return first;
}

function isRetryableRequest(request: Request): boolean {
  const method = request.method.toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS")
    return true;
  return request.headers.has("idempotency-key");
}

export function createRetryingFetch(
  options: { attempts?: number; timeoutMs?: number } = {},
): RetryingFetch {
  const attempts = Math.max(1, options.attempts ?? DEFAULT_ATTEMPTS);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  return async function retryingFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const request = input instanceof Request ? input : new Request(input, init);
    const signal = combineSignals(request.signal, timeoutSignal(timeoutMs));
    const retryable = isRetryableRequest(request);
    let lastError: unknown;

    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const response = await fetch(request.clone(), signal ? { signal } : {});
        const lastAttempt = attempt === attempts - 1;
        if (
          !lastAttempt &&
          retryable &&
          RETRYABLE_STATUSES.has(response.status)
        ) {
          await sleep(backoffFor(attempt));
          continue;
        }
        return response;
      } catch (error) {
        lastError = error;
        const lastAttempt = attempt === attempts - 1;
        if (!retryable || lastAttempt) throw error;
        await sleep(backoffFor(attempt));
      }
    }

    throw lastError instanceof Error
      ? lastError
      : new Error("The TradeFlow API request failed.");
  };
}

/** Shared instance for server-side callers that use plain ``fetch``. */
export const retryingFetch = createRetryingFetch();
