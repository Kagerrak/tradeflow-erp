import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Container readiness probe used by the Lambda Web Adapter.
 *
 * Deliberately dependency-free: it must answer even while the demo is being
 * refreshed and must never open a database connection, because a public health
 * check that touches Aurora would defeat scale-to-zero.
 */
export async function GET() {
  return NextResponse.json(
    { service: "tradeflow-web", status: "ok" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
