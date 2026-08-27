import { NextResponse } from "next/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  DEMO_RESET_MINUTES,
  DEMO_SEED_VERSION,
  nextDemoReset,
} from "@/lib/demo-contract";
import { getServerApiConfig } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export async function GET() {
  const stateDirectory = process.env.TRADEFLOW_DEMO_STATE_DIR;
  let persisted: {
    next_reset_at?: string;
    status?: string;
    seed_version?: string;
  } = {};
  let records: Record<string, unknown> | undefined;
  if (stateDirectory) {
    try {
      persisted = JSON.parse(
        readFileSync(join(stateDirectory, "status.json"), "utf-8"),
      );
      records = JSON.parse(
        readFileSync(join(stateDirectory, "manifest.json"), "utf-8"),
      ).records;
    } catch {
      persisted = { status: "refreshing" };
    }
  }
  const authoritativeStatuses: Record<string, string> = {};
  if (persisted.status !== "refreshing" && records) {
    const config = getServerApiConfig();
    if (config.accessToken) {
      const orders = records.orders as
        | Record<
            string,
            {
              delivery_id?: string;
              fulfillment_order_id?: string;
              order_id?: string;
            }
          >
        | undefined;
      const targets = [
        [
          "approved",
          orders?.approved?.order_id &&
            `/v1/sales/orders/${orders.approved.order_id}`,
        ],
        [
          "picking",
          orders?.partially_picked?.fulfillment_order_id &&
            `/v1/fulfillment/orders/${orders.partially_picked.fulfillment_order_id}/picking-context`,
        ],
        [
          "dispatch",
          orders?.ready_to_dispatch?.fulfillment_order_id &&
            `/v1/fulfillment/orders/${orders.ready_to_dispatch.fulfillment_order_id}/picking-context`,
        ],
        [
          "delivery",
          orders?.posted_invoice?.delivery_id &&
            `/v1/deliveries/${orders.posted_invoice.delivery_id}`,
        ],
      ] as const;
      await Promise.all(
        targets.map(async ([key, path]) => {
          if (!path) return;
          try {
            const response = await fetch(`${config.baseUrl}${path}`, {
              cache: "no-store",
              headers: { Authorization: `Bearer ${config.accessToken}` },
            });
            const value = (await response.json()) as { status?: string };
            if (response.ok && value.status)
              authoritativeStatuses[key] = value.status;
          } catch {
            // Keep the last seeded explanatory status when the private API is unavailable.
          }
        }),
      );
    }
  }
  return NextResponse.json(
    {
      authoritativeStatuses,
      resetIntervalMinutes: DEMO_RESET_MINUTES,
      records,
      seedVersion: persisted.seed_version ?? DEMO_SEED_VERSION,
      status:
        persisted.status ??
        (process.env.TRADEFLOW_DEMO_REFRESHING === "true"
          ? "refreshing"
          : "ready"),
      nextResetAt: persisted.next_reset_at ?? nextDemoReset().toISOString(),
    },
    { headers: { "Cache-Control": "no-store", "X-Robots-Tag": "noindex" } },
  );
}
