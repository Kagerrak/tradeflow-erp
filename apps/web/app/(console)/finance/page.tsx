import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";

const financeModules = [
  {
    description: "Create and post credit memos.",
    href: "/finance/credit-notes",
    title: "Credit notes",
  },
  {
    description: "Record and allocate customer payments.",
    href: "/payments",
    title: "Payment receipts",
  },
  {
    description: "Run customer account statements.",
    href: "/finance/statement",
    title: "Statements",
  },
  {
    description: "View consolidated transaction history.",
    href: "/finance/timeline",
    title: "Timeline",
  },
  {
    description: "Manage payment allocations.",
    href: "/finance/allocations",
    title: "Allocations",
  },
];

export default function FinancePage() {
  return (
    <>
      <PageHeader
        description="Post invoices, manage credit notes, record payments, and review customer statements."
        eyebrow="Commercial"
        title="Finance"
      />

      <section className="dashboard-grid" aria-label="Finance modules">
        {financeModules.map((module) => (
          <Link className="dashboard-tile" href={module.href} key={module.href}>
            <span className="dashboard-tile-title">{module.title}</span>
            <span className="dashboard-tile-desc">{module.description}</span>
          </Link>
        ))}
      </section>
    </>
  );
}
