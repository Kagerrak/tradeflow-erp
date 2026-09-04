"use client";

import {
  customerPaymentTimingLabels,
  type CreateCustomerAccountInput,
  type CustomerCreationState,
  type CustomerDirectoryState,
} from "@tradeflow/customer-directory";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "./ui/badge";
import { DataTable } from "./ui/data-table";
import { EmptyState } from "./ui/empty-state";
import { ErrorState } from "./ui/error-state";
import { PageHeader } from "./ui/page-header";
import { randomId } from "@/lib/random-id";

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
          correlationId: randomId(),
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
            correlationId: randomId(),
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
      <div
        className="workspace-loading"
        role="status"
        aria-label="Loading customer workspace"
      >
        <span className="workspace-loader" aria-hidden="true" />
        <p>Loading customer workspace…</p>
      </div>
    );
  }

  if (workspace.kind === "denied") {
    const unauthenticated = workspace.reason === "unauthenticated";
    const unavailable = workspace.reason === "unavailable";
    const title = unavailable
      ? "Customer service is unavailable"
      : unauthenticated
        ? "Sign in to continue"
        : "Customer access is not assigned";
    const message = unavailable
      ? "The workspace could not confirm your assignment. Try again when service is restored."
      : unauthenticated
        ? "Open your identity provider, then return to this workspace."
        : "Ask an operations administrator to assign customer read access and an operational branch.";
    return (
      <>
        <PageHeader
          description="Search active accounts, check credit standing, and manage commercial terms."
          eyebrow="Commercial"
          title="Customer accounts"
        />
        <ErrorState
          action={
            unavailable ? (
              <button
                className="btn-primary"
                onClick={() => window.location.reload()}
                type="button"
              >
                Retry
              </button>
            ) : undefined
          }
          correlationId={workspace.correlationId}
          title={title}
        >
          <p>{message}</p>
        </ErrorState>
      </>
    );
  }

  const canWrite = workspace.scope.capabilities.includes("customer:write");
  return (
    <>
      <PageHeader
        actions={
          canWrite ? (
            <button
              className="btn-primary"
              type="button"
              aria-label="Open new-account docket"
              onClick={() => setDocketOpen(true)}
            >
              New customer
            </button>
          ) : undefined
        }
        description="Search active accounts, check credit standing, and manage commercial terms."
        eyebrow="Commercial"
        title="Customer accounts"
      />

      <section
        className="directory-panel card"
        aria-labelledby="directory-title"
      >
        <div className="directory-tools">
          <div>
            <span className="section-number">Directory</span>
            <h2 id="directory-title">Accounts</h2>
          </div>
          <div className="scope-stamp">
            <span>Authorized scope</span>
            {workspace.scope.branches.map((branch) => (
              <strong key={branch.branch_id}>
                {branch.name} / {branch.code}
              </strong>
            ))}
          </div>
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
              placeholder="Search by account number or legal name"
            />
            <button className="btn-primary" type="submit">
              Search
            </button>
          </div>
        </form>

        <Directory state={directory} retry={() => void refresh()} />
      </section>

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
    </>
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
      <div className="directory-message" aria-live="polite">
        <span className="workspace-loader" aria-hidden="true" />
        <p>Loading scoped accounts…</p>
      </div>
    );
  }
  if (state.kind !== "ready") {
    const title =
      state.kind === "forbidden"
        ? "Customer access is not assigned"
        : state.kind === "validation"
          ? "Search needs more detail"
          : state.kind === "unauthenticated"
            ? "Sign in to continue"
            : "Directory temporarily unavailable";
    const message =
      state.kind === "forbidden"
        ? "Ask an operations administrator for customer read access and a branch assignment."
        : state.kind === "validation"
          ? "Enter at least two characters in account number or legal name, then search again."
          : state.kind === "unauthenticated"
            ? "Your session expired. Sign in again, then reload this workspace."
            : "Check service status and retry this customer search.";
    return (
      <ErrorState
        action={
          state.kind === "unavailable" ? (
            <button className="btn-primary" onClick={retry} type="button">
              Retry search
            </button>
          ) : state.kind === "unauthenticated" ? (
            <button
              className="btn-primary"
              onClick={() => window.location.reload()}
              type="button"
            >
              Reload after sign-in
            </button>
          ) : undefined
        }
        correlationId={state.correlationId}
        title={title}
      >
        <p>{message}</p>
      </ErrorState>
    );
  }
  if (state.total === 0) {
    return (
      <EmptyState
        description="Create the first account or revise the search terms."
        title="No accounts in this scope"
      />
    );
  }
  return (
    <DataTable>
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
              <Badge>{customer.status}</Badge>
            </td>
            <td>{customerPaymentTimingLabels[customer.paymentTimingPolicy]}</td>
            <td>{customer.creditHold ? "On hold" : "Clear"}</td>
          </tr>
        ))}
      </tbody>
    </DataTable>
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
      idempotencyKeyRef.current ??= randomId();
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
        correlationId: randomId(),
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
            <span className="section-number">New account</span>
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
            <button className="btn-primary" disabled={submitting} type="submit">
              {submitting ? "Creating…" : "Create customer account"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
