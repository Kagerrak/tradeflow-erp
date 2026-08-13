"use client";

import {
  type CreateSupplierInput,
  type SupplierCreationState,
  type SupplierSearchState,
} from "@tradeflow/procurement-suppliers";
import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

type SearchState = SupplierSearchState | { kind: "loading" };
type CreationState = SupplierCreationState | null;

export function ProcurementSuppliersWorkspace() {
  const [search, setSearch] = useState<SearchState>({ kind: "loading" });
  const [query, setQuery] = useState("");
  const [creation, setCreation] = useState<CreationState>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [form, setForm] = useState<CreateSupplierInput>({
    code: "",
    legal_name: "",
    tax_id: "",
    payment_terms: "",
    default_currency: "PHP",
  });

  const fetchSuppliers = async (
    nextQuery: string,
  ): Promise<SupplierSearchState> => {
    const params = new URLSearchParams();
    if (nextQuery.trim().length > 0) {
      params.set("query", nextQuery.trim());
    }
    const response = await fetch(
      `/api/procurement/suppliers?${params.toString()}`,
      {
        cache: "no-store",
        headers: { Accept: "application/json" },
      },
    );
    return (await response.json()) as SupplierSearchState;
  };

  const refresh = async (nextQuery = query) => {
    setSearch({ kind: "loading" });
    try {
      setSearch(await fetchSuppliers(nextQuery));
    } catch {
      setSearch({
        correlationId: crypto.randomUUID(),
        kind: "unavailable",
      });
    }
  };

  useEffect(() => {
    void fetchSuppliers("")
      .then((state) => {
        setSearch(state);
      })
      .catch(() => {
        setSearch({
          correlationId: crypto.randomUUID(),
          kind: "unavailable",
        });
      });
  }, []);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    void refresh(query);
  };

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    setIsCreating(true);
    setCreation(null);
    try {
      const response = await fetch("/api/procurement/suppliers", {
        body: JSON.stringify(form),
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
        },
        method: "POST",
      });
      const state = (await response.json()) as SupplierCreationState;
      setCreation(state);
      if (state.kind === "created") {
        setForm({
          code: "",
          legal_name: "",
          tax_id: "",
          payment_terms: "",
          default_currency: "PHP",
        });
        void refresh(query);
      }
    } catch {
      setCreation({
        correlationId: crypto.randomUUID(),
        kind: "unavailable",
      });
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="procurement-app">
      <header className="procurement-header">
        <Link href="/">TradeFlow</Link>
        <span>Procurement / Suppliers</span>
        <span>Supplier directory</span>
      </header>
      <main className="procurement-main">
        <section className="procurement-title">
          <div>
            <p className="eyebrow">Supplier master data</p>
            <h1>Suppliers.</h1>
          </div>
          <p>
            Maintain the procurement supplier directory. Every supplier is
            scoped to the company and identified by a unique code.
          </p>
        </section>

        <section className="procurement-panel">
          <div className="procurement-section-head">
            <div>
              <span>Procurement / write</span>
              <h2>Register supplier</h2>
            </div>
          </div>

          {creation?.kind === "conflict" && (
            <p className="procurement-message" role="status">
              A supplier with this code already exists.
            </p>
          )}
          {creation?.kind === "validation" && (
            <p className="procurement-message" role="status">
              Check the entered supplier details and try again.
            </p>
          )}
          {creation?.kind === "forbidden" && (
            <p className="procurement-message" role="status">
              You do not have permission to register suppliers.
            </p>
          )}
          {creation?.kind === "created" && (
            <p className="procurement-success" role="status">
              Supplier {creation.supplier.code} registered.
            </p>
          )}

          <form className="procurement-fields" onSubmit={submitCreate}>
            <label>
              Code
              <input
                onChange={(event) =>
                  setForm((current: CreateSupplierInput) => ({
                    ...current,
                    code: event.target.value,
                  }))
                }
                required
                value={form.code}
              />
            </label>
            <label className="procurement-wide">
              Legal name
              <input
                onChange={(event) =>
                  setForm((current: CreateSupplierInput) => ({
                    ...current,
                    legal_name: event.target.value,
                  }))
                }
                required
                value={form.legal_name}
              />
            </label>
            <label>
              Tax ID
              <input
                onChange={(event) =>
                  setForm((current: CreateSupplierInput) => ({
                    ...current,
                    tax_id: event.target.value,
                  }))
                }
                value={form.tax_id ?? ""}
              />
            </label>
            <label>
              Payment terms
              <input
                onChange={(event) =>
                  setForm((current: CreateSupplierInput) => ({
                    ...current,
                    payment_terms: event.target.value,
                  }))
                }
                required
                value={form.payment_terms}
              />
            </label>
            <label>
              Default currency
              <input
                maxLength={3}
                onChange={(event) =>
                  setForm((current: CreateSupplierInput) => ({
                    ...current,
                    default_currency: event.target.value.toUpperCase(),
                  }))
                }
                required
                value={form.default_currency}
              />
            </label>
            <button disabled={isCreating} type="submit">
              Register supplier
            </button>
          </form>
        </section>

        <section className="procurement-panel">
          <div className="procurement-section-head">
            <div>
              <span>Procurement / read</span>
              <h2>Directory</h2>
            </div>
            <form className="procurement-search" onSubmit={submitSearch}>
              <input
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search by code or legal name"
                value={query}
              />
              <button type="submit">Search</button>
            </form>
          </div>

          {search.kind === "loading" && (
            <p className="procurement-message" role="status">
              Loading supplier directory…
            </p>
          )}
          {search.kind === "unavailable" && (
            <p className="procurement-message" role="status">
              Supplier directory unavailable. Retry unchanged work.
            </p>
          )}
          {search.kind === "unauthenticated" && (
            <p className="procurement-message" role="status">
              Sign in to view suppliers.
            </p>
          )}
          {search.kind === "forbidden" && (
            <p className="procurement-message" role="status">
              You do not have permission to view suppliers.
            </p>
          )}

          {search.kind === "ready" && (
            <>
              {search.items.length === 0 ? (
                <p className="procurement-empty">No suppliers found.</p>
              ) : (
                <table className="procurement-table">
                  <thead>
                    <tr>
                      <th>Code</th>
                      <th>Legal name</th>
                      <th>Tax ID</th>
                      <th>Currency</th>
                      <th>Status</th>
                      <th>Version</th>
                    </tr>
                  </thead>
                  <tbody>
                    {search.items.map((supplier) => (
                      <tr key={supplier.supplierId}>
                        <td>{supplier.code}</td>
                        <td>{supplier.legalName}</td>
                        <td>{supplier.taxId ?? "—"}</td>
                        <td>{supplier.defaultCurrency}</td>
                        <td>{supplier.isActive ? "Active" : "Inactive"}</td>
                        <td>{supplier.version}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="procurement-total">Total: {search.total}</p>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
