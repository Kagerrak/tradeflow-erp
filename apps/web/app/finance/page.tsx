import Link from "next/link";
import "./finance.css";

export default function FinancePage() {
  return (
    <div className="finance-app">
      <header className="finance-header">
        <Link href="/">TradeFlow</Link>
        <span>Finance</span>
        <span>Ledger operations</span>
      </header>
      <main className="finance-main">
        <section className="finance-title">
          <div>
            <p className="eyebrow">Operations</p>
            <h1>Finance workspace.</h1>
          </div>
          <p>
            Post invoices, manage credit notes, record payments, and review
            customer statements.
          </p>
        </section>
        <nav className="finance-panel" aria-label="Finance operations">
          <div className="finance-section-head">
            <div>
              <span>Modules</span>
              <h2>Choose a workflow</h2>
            </div>
          </div>
          <ul className="finance-queue">
            <li>
              <Link href="/finance/credit-notes">Credit notes</Link>
            </li>
            <li>
              <Link href="/finance/payment-receipts">Payment receipts</Link>
            </li>
            <li>
              <Link href="/finance/statement">Statements</Link>
            </li>
          </ul>
        </nav>
      </main>
    </div>
  );
}
