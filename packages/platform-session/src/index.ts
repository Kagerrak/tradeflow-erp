import { createTradeFlowClient } from "@tradeflow/api-client";

export type PlatformSessionState =
  | {
      correlationId: string;
      kind: "unauthenticated";
    }
  | {
      correlationId: string;
      kind: "forbidden";
    }
  | {
      correlationId: string;
      kind: "unavailable";
    }
  | {
      correlationId: string;
      database: string;
      kind: "ready";
      service: string;
      user: {
        capabilities: string[];
        displayName: string;
        subject: string;
      };
    };

export const platformStateContent = {
  forbidden: {
    action: "Ask an operations administrator",
    detail:
      "Your identity is valid, but platform:read is not assigned within your operational scope.",
    heading: "Platform access is not assigned",
    index: "HOLD / 403",
    kicker: "Authority required",
    tone: "error" as const,
  },
  unauthenticated: {
    action: "Open your identity provider",
    detail:
      "No active TradeFlow session was found. Sign in through your organization’s provider, then return here.",
    heading: "Sign in to continue",
    index: "HOLD / 401",
    kicker: "Identity required",
    tone: "warning" as const,
  },
  unavailable: {
    action: "Check your connection and try again",
    detail:
      "TradeFlow could not confirm the API and database. No operational changes were posted.",
    heading: "TradeFlow is temporarily unavailable",
    index: "WAIT / 503",
    kicker: "Service interrupted",
    tone: "warning" as const,
  },
} as const;

export type LoadPlatformSessionOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
};

export async function loadPlatformSession({
  accessToken,
  baseUrl,
  correlationId,
  fetch,
}: LoadPlatformSessionOptions): Promise<PlatformSessionState> {
  if (accessToken === undefined || accessToken.length === 0) {
    return {
      correlationId,
      kind: "unauthenticated",
    };
  }

  try {
    const client = createTradeFlowClient({
      accessToken,
      baseUrl,
      correlationId,
      ...(fetch === undefined ? {} : { fetch }),
    });
    const { data, response } = await client.GET("/v1/session");
    if (response.status === 401) {
      return {
        correlationId,
        kind: "unauthenticated",
      };
    }
    if (response.status === 403) {
      return {
        correlationId,
        kind: "forbidden",
      };
    }
    if (response.status >= 500) {
      return {
        correlationId,
        kind: "unavailable",
      };
    }
    if (data === undefined) {
      throw new Error("TradeFlow did not return a platform session.");
    }

    return {
      correlationId,
      database: data.database,
      kind: "ready",
      service: data.service,
      user: {
        capabilities: data.user.capabilities,
        displayName: data.user.display_name,
        subject: data.user.subject,
      },
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return {
        correlationId,
        kind: "unavailable",
      };
    }
    throw error;
  }
}
