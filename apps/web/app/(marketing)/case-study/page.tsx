import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  description: "The domain and engineering decisions behind TradeFlow ERP.",
  title: "Engineering case study",
};

const sections = [
  {
    title: "Business problem",
    heading: "Operational truth was fragmented across handoffs.",
    body: "Wholesale distribution teams need sales, warehouse, delivery, finance, and purchasing to agree about the same transaction. TradeFlow treats the transitions between those teams as first-class business decisions, preserving who authorized each change and what the system accepted.",
  },
  {
    title: "Domain boundaries",
    heading: "One flow, explicit contexts.",
    body: "Commercial owns price, terms, credit exposure, and order approval. Warehouse owns physical custody. Delivery owns evidence and acceptance. Finance owns posting and settlement. Procurement replenishes stock through its own approval chain. Stable identifiers and application commands connect them without collapsing their rules into one model.",
  },
  {
    title: "Order to cash",
    heading: "State advances only through accepted commands.",
    body: "A draft is priced and commercially approved before reservation. Tracked identities are assigned when staff pick physical goods. Dispatch transfers custody to delivery; confirmation records acceptance and triggers invoice posting. Payments remain pending until method-specific evidence is verified, then update allocations and the statement timeline.",
  },
  {
    title: "Inventory custody",
    heading: "Balances are consequences, not editable facts.",
    body: "Availability is derived from immutable movements at a warehouse and location. Picks bind lots and serials to fulfillment work. Transfers, counted variances, delivery exceptions, and corrections add linked movements instead of rewriting history, making every current balance explainable.",
  },
  {
    title: "Authorization",
    heading: "Maker/checker is enforced at the server boundary.",
    body: "Capabilities answer what a person may do, scope answers where, and approval limits answer how much. Distinct internal personas pre-seed approval history for the public demo; visitors operate only as the restricted Demo Operator and cannot administer the organization.",
  },
  {
    title: "Reliability",
    heading: "Retries are normal; duplicate business effects are not.",
    body: "Mutating commands carry idempotency keys and persist command receipts. Aggregate versions provide optimistic concurrency so stale decisions fail explicitly. Correlation identifiers connect interface errors to structured server traces without leaking sensitive context.",
  },
  {
    title: "Testing strategy",
    heading: "Invariants are tested below and above the API.",
    body: "Database tests protect immutability and posting constraints. Contract tests exercise authorized commands and failure shapes. Generated-client checks prevent API drift. Browser and mobile tests verify real workflows, and a migrated PostgreSQL acceptance lane validates the complete stack.",
  },
] as const;

export default function CaseStudyPage() {
  return (
    <>
      <header className="marketing-nav">
        <Link className="marketing-brand" href="/">
          <span aria-hidden="true">TF</span>
          <b>TradeFlow</b>
        </Link>
        <nav aria-label="Case study navigation">
          <Link href="/">Product</Link>
          <a href="#decisions">Decisions</a>
        </nav>
        <Link className="marketing-nav-cta" href="/demo">
          Explore the demo <span aria-hidden="true">↗</span>
        </Link>
      </header>
      <main className="case-study">
        <header className="case-study-header">
          <div>
            <p className="marketing-kicker">Engineering case study</p>
            <h1>Building trust into the handoff.</h1>
          </div>
          <p>
            How I modeled an auditable distribution ERP across commercial
            approval, inventory custody, delivery evidence, invoicing, and
            payment.
          </p>
        </header>
        {sections.map((section, index) => (
          <section
            className="case-study-body"
            id={index === 0 ? "decisions" : undefined}
            key={section.title}
          >
            <h2>
              {String(index + 1).padStart(2, "0")} / {section.title}
            </h2>
            <div className="case-study-copy">
              <h3>{section.heading}</h3>
              <p>{section.body}</p>
            </div>
          </section>
        ))}
        <section className="case-study-body">
          <h2>08 / Tradeoffs</h2>
          <div className="case-study-copy">
            <h3>Deliberate constraints kept the core honest.</h3>
            <div className="case-study-decision">
              <b>Consistency</b>
              <p>
                Server-authoritative posting can add latency, but it prevents
                clients from inventing business state.
              </p>
            </div>
            <div className="case-study-decision">
              <b>Complexity</b>
              <p>
                Immutable corrections create more records than updates, but
                preserve evidence and simplify audit reasoning.
              </p>
            </div>
            <div className="case-study-decision">
              <b>Scope</b>
              <p>
                The portfolio demo uses one shared company and omits tenant
                provisioning, billing, and invitations to focus on operational
                depth.
              </p>
            </div>
            <div className="case-study-decision">
              <b>Limits</b>
              <p>
                Production identity federation, provider-managed observability,
                DNS, and budget alerts remain deployment-specific integrations.
              </p>
            </div>
          </div>
        </section>
        <div className="case-study-cta">
          <p>See the decisions as working operational states.</p>
          <Link className="marketing-primary" href="/demo">
            Open the guided demo <span aria-hidden="true">→</span>
          </Link>
        </div>
      </main>
    </>
  );
}
