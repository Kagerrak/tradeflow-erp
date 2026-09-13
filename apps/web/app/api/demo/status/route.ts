import { NextResponse } from "next/server";
import { DEMO_RESET_MINUTES, DEMO_SEED_VERSION } from "@/lib/demo-contract";
import { readDemoState } from "@/lib/demo-state";

export const dynamic = "force-dynamic";

/**
 * Demo lifecycle for the console.
 *
 * Answered from the API's DynamoDB-backed coordination endpoint, so polling
 * this route never opens a database connection and never prevents Aurora from
 * scaling to zero.
 */
export async function GET() {
  const state = await readDemoState();
  return NextResponse.json(
    {
      lastError: state.lastError,
      nextResetAt: state.nextResetAt,
      records: state.records,
      resetIntervalMinutes: DEMO_RESET_MINUTES,
      seedVersion: state.seedVersion ?? DEMO_SEED_VERSION,
      status: state.status,
    },
    {
      headers: {
        "Cache-Control": "no-store",
        "CDN-Cache-Control": "no-store",
        "X-Robots-Tag": "noindex",
      },
    },
  );
}
