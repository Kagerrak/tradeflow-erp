import "server-only";
import { createHmac } from "node:crypto";

/**
 * Server-only evaluation credential.
 *
 * The web tier calls the business API on behalf of demo visitors. It never
 * sends this credential to the browser and never stores it in a client bundle.
 *
 * The credential is minted on demand from the shared demo signing secret rather
 * than read from a rotating file or parameter:
 *
 * - the demo now refreshes on *renewed activity* instead of every 45 minutes,
 *   so a stored token would silently expire after a long idle period and break
 *   the first visit;
 * - the page tier holds no AWS credentials or AWS API permissions at all.
 *
 * A token is only produced when the demo boundary is provably satisfied
 * (``TRADEFLOW_ENVIRONMENT=demo``, demo mode enabled, and an explicitly
 * demo-named database), mirroring the previous file-credential guard.
 */

const TOKEN_TTL_SECONDS = 2 * 60 * 60;
const REFRESH_MARGIN_SECONDS = 120;

type CachedToken = { token: string; expiresAtSeconds: number };

let cached: CachedToken | undefined;

function requireDemoBoundary(environment: string): void {
  if (environment !== "demo" || process.env.TRADEFLOW_DEMO_MODE !== "enabled") {
    throw new Error(
      "Evaluation credentials require TRADEFLOW_ENVIRONMENT=demo and TRADEFLOW_DEMO_MODE=enabled.",
    );
  }

  const databaseName = process.env.TRADEFLOW_DATABASE_NAME ?? "";
  if (!/^(tradeflow[-_])?demo(?:[-_][a-z0-9]+)?$/i.test(databaseName)) {
    throw new Error(
      "Evaluation credentials require an explicitly demo-named database.",
    );
  }

  if (process.env.TRADEFLOW_PRODUCTION_CONFIGURATION === "true") {
    throw new Error(
      "Evaluation credentials are forbidden with production configuration.",
    );
  }
}

function base64url(value: string): string {
  return Buffer.from(value, "utf-8").toString("base64url");
}

function mintToken(secret: string): CachedToken {
  const issuedAt = Math.floor(Date.now() / 1000);
  const expiresAt = issuedAt + TOKEN_TTL_SECONDS;
  const header = base64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64url(
    JSON.stringify({
      aud: process.env.TRADEFLOW_AUTH_AUDIENCE ?? "tradeflow-api",
      capabilities: ["platform:read", "platform:write"],
      exp: expiresAt,
      iat: issuedAt,
      iss: process.env.TRADEFLOW_AUTH_ISSUER,
      name: "Demo Operator",
      sub: "demo-operator",
    }),
  );
  const signature = createHmac("sha256", secret)
    .update(`${header}.${payload}`)
    .digest("base64url");
  return {
    token: `${header}.${payload}.${signature}`,
    expiresAtSeconds: expiresAt,
  };
}

/**
 * Return the evaluation credential, or ``undefined`` when the deployment has
 * configured a static one instead (local development and the test suites).
 */
export function demoCredential(environment: string): string | undefined {
  // Outside the demo environment configuration supplies its own credential.
  if (environment !== "demo") return undefined;

  requireDemoBoundary(environment);

  const configured = process.env.TRADEFLOW_DEMO_ACCESS_TOKEN;
  if (configured) return configured;

  const secret = process.env.TRADEFLOW_AUTH_TEST_SECRET;
  if (!secret) return undefined;

  const nowSeconds = Math.floor(Date.now() / 1000);
  if (cached && cached.expiresAtSeconds - REFRESH_MARGIN_SECONDS > nowSeconds) {
    return cached.token;
  }
  cached = mintToken(secret);
  return cached.token;
}

/** Test hook: drop the cached token so the next call mints a new one. */
export function resetDemoCredentialCache(): void {
  cached = undefined;
}
