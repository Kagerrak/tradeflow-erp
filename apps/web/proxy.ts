import { NextRequest, NextResponse } from "next/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";

type RateWindow = { count: number; startedAt: number };

const windows = new Map<string, RateWindow>();
const WINDOW_MS = 60_000;
const READ_LIMIT = 120;
const WRITE_LIMIT = 30;
const forbiddenDemoPaths = [
  "/api/organization",
  "/api/admin",
  "/api/users",
  "/api/invitations",
];

function requestIdentity(request: NextRequest): string {
  return (
    request.cookies.get("tradeflow_demo_visitor")?.value ??
    request.headers.get("x-real-ip") ??
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "anonymous"
  );
}

function exceedsRateLimit(request: NextRequest): {
  limited: boolean;
  remaining: number;
} {
  const now = Date.now();
  const mutating = !["GET", "HEAD", "OPTIONS"].includes(request.method);
  const limit = mutating ? WRITE_LIMIT : READ_LIMIT;
  const key = `${requestIdentity(request)}:${mutating ? "write" : "read"}`;
  const current = windows.get(key);
  const window =
    !current || now - current.startedAt >= WINDOW_MS
      ? { count: 0, startedAt: now }
      : current;
  window.count += 1;
  windows.set(key, window);

  if (windows.size > 5_000) {
    for (const [candidate, value] of windows) {
      if (now - value.startedAt >= WINDOW_MS) windows.delete(candidate);
    }
  }

  return {
    limited: window.count > limit,
    remaining: Math.max(0, limit - window.count),
  };
}

function demoIsRefreshing(): boolean {
  const stateDirectory = process.env.TRADEFLOW_DEMO_STATE_DIR;
  if (!stateDirectory) return process.env.TRADEFLOW_DEMO_REFRESHING === "true";
  try {
    const state = JSON.parse(
      readFileSync(join(stateDirectory, "status.json"), "utf-8"),
    ) as { status?: string };
    return state.status !== "ready";
  } catch {
    return true;
  }
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    process.env.TRADEFLOW_ENVIRONMENT === "demo" &&
    forbiddenDemoPaths.some((prefix) => pathname.startsWith(prefix))
  ) {
    return NextResponse.json(
      {
        code: "demo_operation_forbidden",
        message: "Organization administration is disabled in the public demo.",
      },
      { status: 403 },
    );
  }

  if (
    demoIsRefreshing() &&
    pathname.startsWith("/api/") &&
    pathname !== "/api/demo/status"
  ) {
    return NextResponse.json(
      {
        code: "demo_refreshing",
        message: "The demo is refreshing and will be ready shortly.",
      },
      { headers: { "Retry-After": "30" }, status: 503 },
    );
  }

  if (pathname.startsWith("/api/") && pathname !== "/api/demo/status") {
    const rate = exceedsRateLimit(request);
    if (rate.limited) {
      return NextResponse.json(
        {
          code: "demo_rate_limited",
          message:
            "This demo visitor has sent too many requests. Try again in a minute.",
        },
        {
          headers: { "Retry-After": "60", "X-RateLimit-Remaining": "0" },
          status: 429,
        },
      );
    }

    const response = NextResponse.next();
    response.headers.set("X-RateLimit-Remaining", String(rate.remaining));
    response.headers.set("X-Content-Type-Options", "nosniff");
    response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    if (!request.cookies.has("tradeflow_demo_visitor")) {
      response.cookies.set("tradeflow_demo_visitor", crypto.randomUUID(), {
        httpOnly: true,
        maxAge: 60 * 60,
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
      });
    }
    return response;
  }

  return NextResponse.next();
}

export const config = { matcher: ["/api/:path*"] };
