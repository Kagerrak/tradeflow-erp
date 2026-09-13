import createClient from "openapi-fetch";

import { createRetryingFetch } from "./retry";
import type { paths } from "./schema";

export type { components, operations, paths } from "./schema";
export { createRetryingFetch, retryingFetch } from "./retry";

export type TradeFlowClientOptions = {
  accessToken: string;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
};

export function createTraceparent(correlationId: string): string {
  const traceId = correlationId.replaceAll("-", "");
  return `00-${traceId}-${traceId.slice(0, 16)}-01`;
}

const defaultFetch = createRetryingFetch();

export function createTradeFlowClient({
  accessToken,
  baseUrl,
  correlationId,
  fetch,
}: TradeFlowClientOptions) {
  return createClient<paths>({
    baseUrl,
    fetch: fetch ?? defaultFetch,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      traceparent: createTraceparent(correlationId),
      "X-Correlation-ID": correlationId,
    },
  });
}
