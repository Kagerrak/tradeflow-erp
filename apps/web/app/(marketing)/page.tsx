import { ArrowRight, Check, ShieldCheck } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

const workflow = [
  "Approve",
  "Reserve",
  "Pick",
  "Deliver",
  "Invoice",
  "Collect",
];
const teams = [
  ["Sales", "Sales controls pricing, terms, and approvals."],
  ["Warehouse", "Warehouse manages reservations, lots, picks, and transfers."],
  ["Delivery", "Delivery records dispatch, evidence, and customer acceptance."],
  [
    "Finance",
    "Finance posts invoices, verifies receipts, and reconciles balances.",
  ],
] as const;

export default function MarketingPage() {
  return (
    <>
      <a className="marketing-skip" href="#main-content">
        Skip to main content
      </a>
      <header className="marketing-nav">
        <Link className="marketing-brand" href="/" aria-label="TradeFlow home">
          <span aria-hidden="true">TF</span>
          <b>TradeFlow</b>
        </Link>
        <nav aria-label="Primary navigation">
          <a href="#product">Product</a>
          <a href="#solutions">Solutions</a>
          <a href="#workflows">Workflows</a>
          <a href="#security">Security</a>
        </nav>
        <Link className="marketing-nav-cta" href="/demo">
          Open live demo <ArrowRight aria-hidden="true" size={16} />
        </Link>
      </header>

      <main id="main-content" tabIndex={-1}>
        <section className="marketing-hero" aria-labelledby="hero-title">
          <div className="marketing-hero-copy">
            <p className="marketing-overline">
              Distribution operations, connected
            </p>
            <h1 id="hero-title">
              Control every order from sale to settlement.
            </h1>
            <p className="marketing-lede">
              TradeFlow connects sales, inventory, delivery, invoicing, and
              payments in one accountable system.
            </p>
            <div className="marketing-actions">
              <Link className="marketing-primary" href="/demo">
                Open live demo <ArrowRight aria-hidden="true" size={17} />
              </Link>
              <a className="marketing-secondary" href="#workflows">
                See workflows
              </a>
            </div>
            <ul className="hero-assurances" aria-label="Product assurances">
              <li>
                <Check aria-hidden="true" size={15} /> Server-authoritative
                controls
              </li>
              <li>
                <Check aria-hidden="true" size={15} /> Complete operational
                history
              </li>
            </ul>
          </div>
          <div className="hero-product-frame">
            <div className="product-frame-bar">
              <span>TradeFlow / Operations overview</span>
              <span>Live seeded product</span>
            </div>
            <Image
              alt="TradeFlow operations overview showing live work queues and operational metrics"
              className="product-image"
              height={900}
              loading="eager"
              priority
              src="/product/operations-overview.png"
              width={1440}
            />
          </div>
        </section>

        <section
          className="outcome-band"
          id="product"
          aria-labelledby="attention-title"
        >
          <div>
            <span className="section-index">01 / Operations</span>
            <h2 id="attention-title">Know what needs attention.</h2>
          </div>
          <p>
            See approvals, warehouse work, deliveries, invoices, and payments
            from one operational dashboard.
          </p>
        </section>

        <section
          className="workflow-section"
          id="workflows"
          aria-labelledby="workflow-title"
        >
          <div className="section-heading">
            <span className="section-index">02 / Connected workflow</span>
            <h2 id="workflow-title">Keep every order moving.</h2>
            <p>
              TradeFlow guides each order through the next valid action while
              preserving inventory, credit, and authorization controls.
            </p>
          </div>
          <ol className="workflow-track">
            {workflow.map((stage, index) => (
              <li key={stage}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{stage}</strong>
                {index < workflow.length - 1 && (
                  <ArrowRight aria-hidden="true" size={18} />
                )}
              </li>
            ))}
          </ol>
        </section>

        <section
          className="teams-section"
          id="solutions"
          aria-labelledby="teams-title"
        >
          <div className="teams-intro">
            <span className="section-index">03 / Role-based work</span>
            <h2 id="teams-title">One system for every team.</h2>
            <p>
              Each role gets focused workspaces while every accepted decision
              updates the same operational record.
            </p>
          </div>
          <div className="team-ledger">
            {teams.map(([name, detail], index) => (
              <article key={name}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{name}</h3>
                <p>{detail}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="product-preview" aria-labelledby="preview-title">
          <div className="section-heading compact">
            <span className="section-index">04 / Product views</span>
            <h2 id="preview-title">See the product in action.</h2>
          </div>
          <div className="preview-layout">
            <figure className="preview-main">
              <Image
                alt="TradeFlow operations dashboard with action queue"
                height={900}
                src="/product/operations-overview.png"
                width={1440}
              />
              <figcaption>
                <b>Operations dashboard</b>
                <span>Prioritized work across every team</span>
              </figcaption>
            </figure>
            <div className="preview-stack">
              <figure>
                <Image
                  alt="TradeFlow warehouse picking workflow"
                  height={900}
                  src="/product/order-warehouse.png"
                  width={1440}
                />
                <figcaption>
                  <b>Order and warehouse workflow</b>
                  <span>Valid actions, custody, and traceability</span>
                </figcaption>
              </figure>
              <figure>
                <Image
                  alt="TradeFlow invoice, payment, and statement workspace"
                  height={900}
                  src="/product/finance-statement.png"
                  width={1440}
                />
                <figcaption>
                  <b>Invoice, payment, and statement</b>
                  <span>
                    One receivables history from posting to allocation
                  </span>
                </figcaption>
              </figure>
            </div>
          </div>
        </section>

        <section
          className="trust-section"
          id="security"
          aria-labelledby="trust-title"
        >
          <div className="trust-mark" aria-hidden="true">
            <ShieldCheck size={30} />
          </div>
          <div>
            <span className="section-index">05 / Trust by design</span>
            <h2 id="trust-title">Every decision leaves a clear record.</h2>
          </div>
          <p>
            TradeFlow protects approvals, stock custody, delivery evidence, and
            financial postings with server-authoritative controls and complete
            history.
          </p>
        </section>

        <section className="marketing-final" aria-labelledby="final-title">
          <div>
            <h2 id="final-title">
              Run the complete order-to-payment workflow.
            </h2>
            <p>Explore TradeFlow using realistic, seeded operational data.</p>
          </div>
          <Link className="marketing-primary" href="/demo">
            Open live demo <ArrowRight aria-hidden="true" size={17} />
          </Link>
        </section>
      </main>

      <footer className="marketing-footer">
        <Link className="marketing-brand" href="/">
          <span aria-hidden="true">TF</span>
          <b>TradeFlow</b>
        </Link>
        <p>Accountable order-to-payment operations.</p>
        <div>
          <Link href="/case-study">Engineering case study</Link>
          <a href="https://github.com/Kagerrak/tradeflow-erp">Source</a>
        </div>
      </footer>
    </>
  );
}
