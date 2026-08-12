"use client";

import {
  customerPaymentTimingLabels,
  type CreateCustomerAccountInput,
  type CustomerCreationState,
  type CustomerDirectoryState,
} from "@tradeflow/customer-directory";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

type Branch = {
  branch_id: string;
  code: string;
  is_active: boolean;
  name: string;
  version: number;
};

type Scope =
  | {
      branches: Branch[];
      capabilities: string[];
      user: {
        display_name: string;
        is_operations_administrator: boolean;
        subject: string;
      };
      warehouses: unknown[];
    }
  | {
      correlationId: string;
      kind: "forbidden" | "unauthenticated" | "unavailable";
    };

type WorkspaceState =
  | { kind: "loading" }
  | {
      correlationId: string;
      kind: "denied";
      reason: "forbidden" | "unauthenticated" | "unavailable";
    }
  | { kind: "ready"; scope: Extract<Scope, { branches: Branch[] }> };

async function loadScope(): Promise<Scope> {
  const response = await fetch("/api/customer-scope", {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  return (await response.json()) as Scope;
}

async function searchCustomers(query: string): Promise<CustomerDirectoryState> {
  const response = await fetch(
    `/api/customers?query=${encodeURIComponent(query)}`,
    {
      cache: "no-store",
      headers: { Accept: "application/json" },
    },
  );
  return (await response.json()) as CustomerDirectoryState;
}

export function CustomerWorkspace() {
  const [workspace, setWorkspace] = useState<WorkspaceState>({
    kind: "loading",
  });
  const [directory, setDirectory] = useState<
    CustomerDirectoryState | { kind: "loading" }
  >({
    kind: "loading",
  });
  const [query, setQuery] = useState("");
  const [docketOpen, setDocketOpen] = useState(false);
  const [creation, setCreation] = useState<CustomerCreationState | null>(null);
  const closeDocket = useCallback(() => {
    setDocketOpen(false);
    setCreation(null);
  }, []);

  const refresh = useCallback(
    async (nextQuery = query) => {
      setDirectory({ kind: "loading" });
      try {
        setDirectory(await searchCustomers(nextQuery));
      } catch {
        setDirectory({
          correlationId: crypto.randomUUID(),
          kind: "unavailable",
        });
      }
    },
    [query],
  );

  useEffect(() => {
    let active = true;
    void loadScope()
      .then((scope) => {
        if (!active) return;
        if ("kind" in scope) {
          setWorkspace({
            correlationId: scope.correlationId,
            kind: "denied",
            reason: scope.kind,
          });
          return;
        }
        setWorkspace({ kind: "ready", scope });
        void searchCustomers("").then((state) => {
          if (active) setDirectory(state);
        });
      })
      .catch(() => {
        if (active) {
          setWorkspace({
            correlationId: crypto.randomUUID(),
            kind: "denied",
            reason: "unavailable",
          });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (workspace.kind === "loading") {
    return (
      <main
        className="customer-loading"
        role="status"
        aria-label="Loading customer workspace"
      >
        <span className="customer-loader" aria-hidden="true" />
        <p>Verifying operational scope</p>
        <h1>Loading customer workspace…</h1>
      </main>
    );
  }

  if (workspace.kind === "denied") {
    const unauthenticated = workspace.reason === "unauthenticated";
    const unavailable = workspace.reason === "unavailable";
    return (
      <main className="customer-denied">
        <Link className="customer-wordmark" href="/">
          TradeFlow / Customers
        </Link>
        <p className="eyebrow">Access boundary</p>
        <h1>
          {unavailable
            ? "Customer service is unavailable"
            : unauthenticated
              ? "Sign in to continue"
              : "Customer access is not assigned"}
        </h1>
        <p>
          {unavailable
            ? "The workspace could not confirm your assignment. Try again when service is restored."
            : unauthenticated
              ? "Open your identity provider, then return to this workspace."
              : "Ask an operations administrator to assign customer read access and an operational Branch."}
        </p>
        <p className="support-reference">
          Support reference <code>{workspace.correlationId}</code>
        </p>
        <a className="text-link" href="/customers">
          Retry workspace →
        </a>
      </main>
    );
  }

  const canWrite = workspace.scope.capabilities.includes("customer:write");
  return (
    <div className="customer-app">
      <header className="customer-header">
        <Link className="customer-wordmark" href="/">
          TradeFlow
        </Link>
        <span>Customer accounts</span>
        <span className="operator">{workspace.scope.user.display_name}</span>
      </header>

      <main className="customer-main">
        <section className="customer-title">
          <div>
            <p className="eyebrow">Commercial directory / 003</p>
            <h1>Know the account before the order.</h1>
          </div>
          <div className="scope-stamp">
            <span>Authorized scope</span>
            {workspace.scope.branches.map((branch) => (
              <strong key={branch.branch_id}>
                {branch.name} / {branch.code}
              </strong>
            ))}
          </div>
        </section>

        <section className="directory-panel" aria-labelledby="directory-title">
          <div className="directory-tools">
            <div>
              <p className="section-number">01 / Directory</p>
              <h2 id="directory-title">Customer accounts</h2>
            </div>
            {canWrite && (
              <button
                className="primary-action"
                type="button"
                onClick={() => setDocketOpen(true)}
              >
                Open new-account docket
              </button>
            )}
          </div>

          <form
            className="search-strip"
            onSubmit={(event) => {
              event.preventDefault();
              void refresh();
            }}
          >
            <label htmlFor="customer-query">
              Search account number or legal name
            </label>
            <div>
              <input
                id="customer-query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="e.g. MNL-0042 or Northstar"
              />
              <button type="submit">Search</button>
            </div>
          </form>

          <Directory state={directory} retry={() => void refresh()} />
        </section>
      </main>

      {docketOpen && (
        <CustomerDocket
          branches={workspace.scope.branches.filter(
            (branch) => branch.is_active,
          )}
          creation={creation}
          onClose={closeDocket}
          onCreated={async (state) => {
            setCreation(state);
            if (state.kind === "created") {
              await refresh();
            }
          }}
        />
      )}
    </div>
  );
}

function Directory({
  retry,
  state,
}: {
  retry: () => void;
  state: CustomerDirectoryState | { kind: "loading" };
}) {
  if (state.kind === "loading") {
    return (
      <p className="directory-message" aria-live="polite">
        Loading scoped accounts…
      </p>
    );
  }
  if (state.kind !== "ready") {
    const heading =
      state.kind === "forbidden"
        ? "Customer access is not assigned"
        : state.kind === "validation"
          ? "Search needs more detail"
          : state.kind === "unauthenticated"
            ? "Sign in to continue"
            : "Directory temporarily unavailable";
    const message =
      state.kind === "forbidden"
        ? "Ask an operations administrator for customer read access and a Branch assignment."
        : state.kind === "validation"
          ? "Enter at least two characters in account number or legal name, then search again."
          : state.kind === "unauthenticated"
            ? "Your session expired. Sign in again, then reload this workspace."
            : "Check service status and retry this customer search.";
    return (
      <div className="directory-message">
        <h3>{heading}</h3>
        <p>{message}</p>
        <p>
          Support reference <code>{state.correlationId}</code>
        </p>
        {state.kind === "unavailable" && (
          <button onClick={retry}>Retry search</button>
        )}
        {state.kind === "unauthenticated" && (
          <button onClick={() => window.location.reload()}>
            Reload after sign-in
          </button>
        )}
      </div>
    );
  }
  if (state.total === 0) {
    return (
      <div className="directory-message directory-empty">
        <span aria-hidden="true">∅</span>
        <h3>No accounts in this scope</h3>
        <p>Create the first account or revise the search terms.</p>
      </div>
    );
  }
  return (
    <div className="directory-table-wrap">
      <table className="directory-table">
        <thead>
          <tr>
            <th>Account</th>
            <th>Legal name</th>
            <th>Status</th>
            <th>Payment timing</th>
            <th>Credit</th>
          </tr>
        </thead>
        <tbody>
          {state.items.map((customer) => (
            <tr key={customer.customerId}>
              <td>
                <code>{customer.accountNumber}</code>
              </td>
              <td>{customer.legalName}</td>
              <td>
                <span className={`account-status status-${customer.status}`}>
                  {customer.status}
                </span>
              </td>
              <td>
                {customerPaymentTimingLabels[customer.paymentTimingPolicy]}
              </td>
              <td>{customer.creditHold ? "On hold" : "Clear"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CustomerDocket({
  branches,
  creation,
  onClose,
  onCreated,
}: {
  branches: Branch[];
  creation: CustomerCreationState | null;
  onClose: () => void;
  onCreated: (state: CustomerCreationState) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const idempotencyKeyRef = useRef<string | null>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          'button, input, select, [href], [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => !element.hasAttribute("disabled"));
    focusable()[0]?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0];
      const last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    const data = new FormData(event.currentTarget);
    const value = (name: string) => String(data.get(name) ?? "");
    const nullable = (name: string) => value(name).trim() || null;
    const address = (kind: "billing" | "delivery") => ({
      address_key: kind.toUpperCase(),
      city: value(`${kind}_city`),
      country_code: value(`${kind}_country`).toUpperCase(),
      kind,
      line_1: value(`${kind}_line_1`),
      line_2: nullable(`${kind}_line_2`),
      postal_code: value(`${kind}_postal_code`),
      region: value(`${kind}_region`),
    });
    const command: CreateCustomerAccountInput = {
      account_number: value("account_number").toUpperCase(),
      addresses: [address("billing"), address("delivery")],
      branch_id: value("branch_id"),
      contacts: [
        {
          email: nullable("contact_email"),
          name: value("contact_name"),
          phone: nullable("contact_phone"),
          role: value("contact_role"),
        },
      ],
      credit_hold: data.get("credit_hold") === "on",
      credit_limit: nullable("credit_limit"),
      legal_name: value("legal_name"),
      payment_terms: value("payment_terms"),
      payment_timing_policy: value(
        "payment_timing",
      ) as CreateCustomerAccountInput["payment_timing_policy"],
      status: value("status") as CreateCustomerAccountInput["status"],
    };
    try {
      idempotencyKeyRef.current ??= crypto.randomUUID();
      const response = await fetch("/api/customers", {
        body: JSON.stringify(command),
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKeyRef.current,
        },
        method: "POST",
      });
      await onCreated((await response.json()) as CustomerCreationState);
    } catch {
      await onCreated({
        correlationId: crypto.randomUUID(),
        kind: "unavailable",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="docket-backdrop">
      <section
        className="docket"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="docket-title"
      >
        <header>
          <div>
            <p className="section-number">02 / New account</p>
            <h2 id="docket-title">Customer account docket</h2>
          </div>
          <button className="close-action" type="button" onClick={onClose}>
            Close
          </button>
        </header>
        {creation?.kind === "created" && (
          <p className="form-success" role="status">
            {creation.customer.accountNumber} created. Directory refreshed.
          </p>
        )}
        {creation !== null && creation.kind !== "created" && (
          <p className="form-error" role="alert">
            {creation.kind === "conflict"
              ? "That account number already exists. Keep this docket open and choose another."
              : creation.kind === "forbidden"
                ? "Your assignment does not allow customer creation."
                : creation.kind === "unauthenticated"
                  ? "Your session expired. Sign in again, then return to this docket."
                  : creation.kind === "unavailable"
                    ? "Customer service is unavailable. Keep this docket open and retry when service returns."
                    : "Check the docket fields and submit again."}{" "}
            Support reference <code>{creation.correlationId}</code>
          </p>
        )}
        <form className="docket-form" onSubmit={(event) => void submit(event)}>
          <fieldset>
            <legend>Account identity</legend>
            <label>
              Legal name
              <input name="legal_name" required />
            </label>
            <label>
              Account number
              <input name="account_number" required />
            </label>
            <label>
              Branch
              <select name="branch_id">
                {branches.map((branch) => (
                  <option key={branch.branch_id} value={branch.branch_id}>
                    {branch.code} — {branch.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select name="status" defaultValue="active">
                <option value="active">Active</option>
                <option value="prospect">Prospect</option>
                <option value="inactive">Inactive</option>
              </select>
            </label>
          </fieldset>
          <fieldset>
            <legend>Commercial terms</legend>
            <label>
              Payment timing
              <select name="payment_timing" defaultValue="prepaid">
                <option value="prepaid">Prepaid</option>
                <option value="cash_on_delivery">Cash on delivery</option>
                <option value="on_account">On account / credit</option>
              </select>
            </label>
            <label>
              Payment terms
              <input
                name="payment_terms"
                defaultValue="Due before release"
                required
              />
            </label>
            <label>
              Credit limit
              <input
                name="credit_limit"
                inputMode="decimal"
                placeholder="Optional"
              />
            </label>
            <label className="checkbox-field">
              <input name="credit_hold" type="checkbox" /> Place on credit hold
            </label>
          </fieldset>
          <fieldset>
            <legend>Primary contact</legend>
            <label>
              Contact name
              <input name="contact_name" required />
            </label>
            <label>
              Contact role
              <input name="contact_role" required />
            </label>
            <label>
              Email
              <input name="contact_email" type="email" />
            </label>
            <label>
              Phone
              <input name="contact_phone" type="tel" />
            </label>
          </fieldset>
          {(["billing", "delivery"] as const).map((kind) => (
            <fieldset key={kind}>
              <legend>
                {kind === "billing" ? "Billing address" : "Delivery address"}
              </legend>
              <label>
                Address line 1
                <input name={`${kind}_line_1`} required />
              </label>
              <label>
                Address line 2<input name={`${kind}_line_2`} />
              </label>
              <label>
                City
                <input name={`${kind}_city`} required />
              </label>
              <label>
                Region
                <input name={`${kind}_region`} required />
              </label>
              <label>
                Postal code
                <input name={`${kind}_postal_code`} required />
              </label>
              <label>
                Country code
                <input name={`${kind}_country`} maxLength={2} required />
              </label>
            </fieldset>
          ))}
          <div className="docket-actions">
            <span>Submission is idempotent and auditable.</span>
            <button
              className="primary-action"
              disabled={submitting}
              type="submit"
            >
              {submitting ? "Creating…" : "Create customer account"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
