import "server-only";
import { readFileSync } from "node:fs";

import { demoCredential } from "./demo-credential";

export type ServerApiConfig = Readonly<{
  accessToken: string | undefined;
  baseUrl: string;
  environment: string;
}>;

function credentialFromFile(): string | undefined {
  const credentialFile = process.env.TRADEFLOW_DEMO_CREDENTIAL_FILE;
  if (!credentialFile) return undefined;
  try {
    return readFileSync(credentialFile, "utf-8").trim();
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    return undefined;
  }
}

export function getServerApiConfig(): ServerApiConfig {
  const environment = process.env.TRADEFLOW_ENVIRONMENT ?? "development";
  const configured =
    process.env.TRADEFLOW_DEMO_ACCESS_TOKEN ?? credentialFromFile();
  const accessToken =
    configured ??
    demoCredential(environment) ??
    process.env.TRADEFLOW_WEB_TEST_ACCESS_TOKEN;

  return {
    accessToken,
    baseUrl: process.env.TRADEFLOW_API_URL ?? "http://127.0.0.1:8000",
    environment,
  };
}

export function authorizationHeaders(): HeadersInit {
  const { accessToken } = getServerApiConfig();
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}
