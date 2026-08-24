import { readFileSync } from "node:fs";

export type ServerApiConfig = Readonly<{
  accessToken: string | undefined;
  baseUrl: string;
  environment: string;
}>;

function requireDemoBoundary(environment: string): void {
  if (environment !== "demo" || process.env.TRADEFLOW_DEMO_MODE !== "enabled") {
    throw new Error(
      "Demo credentials require TRADEFLOW_ENVIRONMENT=demo and TRADEFLOW_DEMO_MODE=enabled.",
    );
  }

  const databaseName = process.env.TRADEFLOW_DATABASE_NAME ?? "";
  if (!/^(tradeflow[-_])?demo(?:[-_][a-z0-9]+)?$/i.test(databaseName)) {
    throw new Error(
      "Demo credentials require an explicitly demo-named database.",
    );
  }

  if (process.env.TRADEFLOW_PRODUCTION_CONFIGURATION === "true") {
    throw new Error(
      "Demo credentials are forbidden with production configuration.",
    );
  }
}

export function getServerApiConfig(): ServerApiConfig {
  const environment = process.env.TRADEFLOW_ENVIRONMENT ?? "development";
  const credentialFile = process.env.TRADEFLOW_DEMO_CREDENTIAL_FILE;
  const fileAccessToken = credentialFile
    ? readFileSync(credentialFile, "utf-8").trim()
    : undefined;
  const demoAccessToken =
    process.env.TRADEFLOW_DEMO_ACCESS_TOKEN ?? fileAccessToken;

  if (demoAccessToken) requireDemoBoundary(environment);

  return {
    accessToken: demoAccessToken ?? process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    environment,
  };
}

export function authorizationHeaders(): HeadersInit {
  const { accessToken } = getServerApiConfig();
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}
