"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { demoRecords } from "@/lib/demo-contract";

type DemoStatus = {
  authoritativeStatuses?: Record<string, string>;
  nextResetAt: string;
  records?: {
    customers?: Record<string, string>;
    orders?: Record<
      string,
      { delivery_id?: string; fulfillment_order_id?: string; order_id?: string }
    >;
  };
  status: "failed" | "ready" | "refreshing";
};

const fallbackSteps = [
  {
    demonstrate:
      "Commercial terms, credit exposure, and maker/checker approval remain visible on the order.",
    explanation:
      "Start with an order that a different internal persona has commercially approved.",
    href: `/sales-orders/approvals?orderId=${demoRecords.approvedOrder}&from=demo`,
    status: "Approved · ready to pick",
    title: "Inspect the approved order",
  },
  {
    demonstrate:
      "The warehouse assigns tracked stock identities only when physical goods are picked.",
    explanation:
      "Open the prepared fulfillment work and inspect its reserved lots and quantities.",
    href: `/picking?fulfillmentOrderId=${demoRecords.fulfillmentOrder}&from=demo`,
    status: "Prepared · 24 units reserved",
    title: "Review prepared picking work",
  },
  {
    demonstrate:
      "Dispatch is an explicit custody transfer, protected by authoritative server state.",
    explanation:
      "Continue to the dispatch workbench for the same staged inventory.",
    href: `/dispatch?fulfillmentOrderId=${demoRecords.fulfillmentOrder}&from=demo`,
    status: "Staged · ready to dispatch",
    title: "Dispatch staged inventory",
  },
  {
    demonstrate:
      "Delivery evidence drives immutable invoice, payment, statement, and timeline records.",
    explanation:
      "Review the resulting delivery, then follow its financial trail.",
    href: `/deliveries?deliveryId=${demoRecords.delivery}&customerId=${demoRecords.customer}&from=demo`,
    status: "Confirmed · invoice posted",
    title: "Trace delivery to settlement",
  },
] as const;

export function DemoScenario() {
  const [demoStatus, setDemoStatus] = useState<DemoStatus | null>(null);

  useEffect(() => {
    void fetch("/api/demo/status", { cache: "no-store" })
      .then((response) => response.json() as Promise<DemoStatus>)
      .then(setDemoStatus)
      .catch(() => setDemoStatus(null));
  }, []);

  const resetLabel = demoStatus?.nextResetAt
    ? new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
      }).format(new Date(demoStatus.nextResetAt))
    : "on a 45-minute cycle";
  const records = demoStatus?.records;
  const approved = records?.orders?.approved;
  const picking = records?.orders?.partially_picked;
  const dispatch = records?.orders?.ready_to_dispatch;
  const delivery = records?.orders?.posted_invoice;
  const steps = records
    ? [
        {
          ...fallbackSteps[0],
          href: `/sales-orders/approvals?orderId=${approved?.order_id ?? demoRecords.approvedOrder}&from=demo`,
        },
        {
          ...fallbackSteps[1],
          href: `/picking?fulfillmentOrderId=${picking?.fulfillment_order_id ?? demoRecords.fulfillmentOrder}&from=demo`,
        },
        {
          ...fallbackSteps[2],
          href: `/dispatch?fulfillmentOrderId=${dispatch?.fulfillment_order_id ?? demoRecords.fulfillmentOrder}&from=demo`,
        },
        {
          ...fallbackSteps[3],
          href: `/deliveries?deliveryId=${delivery?.delivery_id ?? demoRecords.delivery}&customerId=${records.customers?.HARBOR ?? demoRecords.customer}&from=demo`,
        },
      ]
    : fallbackSteps;
  const liveStatuses = demoStatus?.authoritativeStatuses;

  return (
    <section className="demo-scenario" aria-labelledby="demo-scenario-title">
      <header className="demo-scenario-header">
        <div>
          <span className="dashboard-eyebrow">
            Guided order-to-cash journey
          </span>
          <h2 id="demo-scenario-title">Follow one accountable handoff.</h2>
        </div>
        <div
          className={`demo-reset-state demo-reset-${demoStatus?.status ?? "ready"}`}
          role="status"
        >
          <span aria-hidden="true" />
          {demoStatus?.status === "refreshing"
            ? "Demo is refreshing"
            : demoStatus?.status === "failed"
              ? "Demo refresh needs attention"
              : `Next reset ${resetLabel}`}
        </div>
      </header>
      <p className="demo-scenario-intro">
        The records are pre-seeded at useful lifecycle stages. If another
        visitor advances a record, its authoritative status may differ from the
        guide; the demo restores automatically.
      </p>
      <ol className="demo-steps">
        {steps.map((step, index) => (
          <li key={step.title}>
            <span className="demo-step-number">
              {String(index + 1).padStart(2, "0")}
            </span>
            <div className="demo-step-copy">
              <h3>{step.title}</h3>
              <p>{step.explanation}</p>
              <small>
                <b>What this demonstrates:</b> {step.demonstrate}
              </small>
            </div>
            <div className="demo-step-action">
              <span>
                <i aria-hidden="true" />
                {liveStatuses?.[
                  ["approved", "picking", "dispatch", "delivery"][index] ?? ""
                ]?.replaceAll("_", " ") ?? step.status}
              </span>
              <Link href={step.href}>
                Open record <span aria-hidden="true">→</span>
              </Link>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
