import Link from "next/link";

const lifecycle = [
  ["01", "Approve", "Price, terms, credit exposure"],
  ["02", "Reserve", "Warehouse-level availability"],
  ["03", "Pick", "Lot and custody assignment"],
  ["04", "Deliver", "Evidence and acceptance"],
  ["05", "Settle", "Invoice, payment, statement"],
] as const;

export default function MarketingPage() {
  return (
    <>
      <a className="marketing-skip" href="#story">
        Skip to product story
      </a>
      <header className="marketing-nav">
        <Link className="marketing-brand" href="/" aria-label="TradeFlow home">
          <span aria-hidden="true">TF</span>
          <b>TradeFlow</b>
        </Link>
        <nav aria-label="Product navigation">
          <a href="#workflow">Workflow</a>
          <a href="#engineering">Engineering</a>
          <Link href="/case-study">Case study</Link>
        </nav>
        <Link className="marketing-nav-cta" href="/demo">
          Explore the demo <span aria-hidden="true">↗</span>
        </Link>
      </header>

      <main id="story">
        <section className="marketing-hero" aria-labelledby="hero-title">
          <div className="marketing-hero-copy">
            <p className="marketing-kicker">
              Distribution ERP / portfolio release
            </p>
            <h1 id="hero-title">
              Every handoff.
              <br />
              <em>One accountable flow.</em>
            </h1>
            <p className="marketing-lede">
              TradeFlow connects commercial approval, warehouse custody,
              delivery evidence, invoicing, and payment—without hiding business
              state behind a generic dashboard.
            </p>
            <div className="marketing-actions">
              <Link className="marketing-primary" href="/demo">
                Explore the live demo <span aria-hidden="true">→</span>
              </Link>
              <a
                className="marketing-secondary"
                href="https://github.com/Kagerrak/tradeflow-erp"
              >
                View source <span aria-hidden="true">↗</span>
              </a>
            </div>
          </div>

          <div
            className="product-docket"
            aria-label="Representative TradeFlow order control desk"
          >
            <div className="docket-head">
              <span>COMMERCIAL ORDER</span>
              <span>SO-2026-0142</span>
            </div>
            <div className="docket-state">
              <span className="docket-seal" aria-hidden="true">
                ✓
              </span>
              <div>
                <small>AUTHORITATIVE STATUS</small>
                <strong>Approved · ready to pick</strong>
              </div>
            </div>
            <dl className="docket-facts">
              <div>
                <dt>Customer</dt>
                <dd>Harbor &amp; Pine Retail</dd>
              </div>
              <div>
                <dt>Terms</dt>
                <dd>Net 30 · within limit</dd>
              </div>
              <div>
                <dt>Branch</dt>
                <dd>Manila / MNL-01</dd>
              </div>
              <div>
                <dt>Order total</dt>
                <dd>₱ 184,760.00</dd>
              </div>
            </dl>
            <div className="docket-lines">
              <span>2 lines reserved</span>
              <span>24 units</span>
              <span>3 tracked lots</span>
            </div>
            <div className="docket-next">
              <small>NEXT VALID ACTION</small>
              <b>Prepare picking work</b>
              <span aria-hidden="true">→</span>
            </div>
          </div>
        </section>

        <section className="marketing-proof" aria-label="Product scope">
          <p>Built for a real operational boundary—not a CRUD showcase.</p>
          <ul>
            <li>Web + mobile</li>
            <li>Immutable ledgers</li>
            <li>Maker / checker</li>
            <li>Offline-aware</li>
          </ul>
        </section>

        <section
          className="marketing-workflow"
          id="workflow"
          aria-labelledby="workflow-title"
        >
          <div className="marketing-section-intro">
            <p className="marketing-kicker">Order to payment</p>
            <h2 id="workflow-title">The business story stays visible.</h2>
            <p>
              Each stage opens only when the server accepts the previous
              decision. Visitors can inspect records at every lifecycle state in
              the seeded demo.
            </p>
          </div>
          <ol className="lifecycle-track">
            {lifecycle.map(([number, title, detail]) => (
              <li key={number}>
                <span>{number}</span>
                <div>
                  <b>{title}</b>
                  <small>{detail}</small>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="marketing-problem">
          <div>
            <p className="marketing-kicker">The problem</p>
            <h2>Distribution work breaks at the seams.</h2>
          </div>
          <p>
            Orders live in spreadsheets. Stock truth arrives late. Delivery
            evidence sits in chat threads. Finance reconstructs what happened
            after the fact.
          </p>
          <p>
            TradeFlow makes authorization, custody, payment condition, and
            posting state explicit—so a handoff is a recorded business decision,
            not an assumption.
          </p>
        </section>

        <section
          className="engineering-field"
          id="engineering"
          aria-labelledby="engineering-title"
        >
          <div className="marketing-section-intro">
            <p className="marketing-kicker">Engineering depth</p>
            <h2 id="engineering-title">Trust is part of the interface.</h2>
          </div>
          <div className="engineering-map">
            <article>
              <span>DOMAIN</span>
              <h3>Bounded operational contexts</h3>
              <p>
                Commercial, warehouse, delivery, finance, and procurement rules
                remain explicit while sharing authoritative identities.
              </p>
            </article>
            <article>
              <span>INTEGRITY</span>
              <h3>Immutable source movements</h3>
              <p>
                Stock custody and financial balances are derived from accepted
                movements, reversals, and linked corrections.
              </p>
            </article>
            <article>
              <span>AUTHORITY</span>
              <h3>Capability + scope + limit</h3>
              <p>
                Maker/checker decisions are server-authoritative, idempotent,
                and protected by optimistic concurrency.
              </p>
            </article>
            <article>
              <span>PLATFORM</span>
              <h3>One contract, three surfaces</h3>
              <p>
                FastAPI and PostgreSQL serve generated TypeScript clients across
                Next.js, Expo, and background workers.
              </p>
            </article>
          </div>
        </section>

        <section className="role-ledger" aria-labelledby="role-title">
          <div>
            <p className="marketing-kicker">My role</p>
            <h2 id="role-title">
              Product thinking through production invariants.
            </h2>
          </div>
          <div className="role-copy">
            <p>
              I designed and implemented TradeFlow end to end: domain
              boundaries, authorization rules, API contracts, ledger models, web
              and mobile workflows, test strategy, and deployment shape.
            </p>
            <Link href="/case-study">
              Read the engineering case study <span aria-hidden="true">→</span>
            </Link>
          </div>
        </section>

        <section className="marketing-final">
          <p className="marketing-kicker">A guided ten-minute review</p>
          <h2>Follow an approved order all the way to a customer statement.</h2>
          <Link className="marketing-primary" href="/demo">
            Open the live demo <span aria-hidden="true">→</span>
          </Link>
        </section>
      </main>

      <footer className="marketing-footer">
        <Link className="marketing-brand" href="/">
          <span aria-hidden="true">TF</span>
          <b>TradeFlow</b>
        </Link>
        <p>Auditable distribution operations.</p>
        <div>
          <a href="https://github.com/Kagerrak/tradeflow-erp">Source</a>
          <Link href="/case-study">Case study</Link>
        </div>
      </footer>
    </>
  );
}
