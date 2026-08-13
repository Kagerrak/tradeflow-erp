import { describe, expect, it } from "vitest";

import {
  createSupplier,
  searchSuppliers,
  type CreateSupplierInput,
} from "./index";

const supplierInput: CreateSupplierInput = {
  code: "ACME-001",
  default_currency: "PHP",
  legal_name: "ACME Supplies Inc.",
  payment_terms: "Net 30",
  tax_id: "123-456-789",
};

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
    status,
  });
}

describe("procurement suppliers journey", () => {
  it("returns the server-authorized supplier list", async () => {
    const state = await searchSuppliers({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "012f42cd-c035-42ff-987a-5919bd7396aa",
      fetch: () =>
        Promise.resolve(
          jsonResponse(
            {
              items: [
                {
                  code: "ACME-001",
                  default_currency: "PHP",
                  is_active: true,
                  legal_name: "ACME Supplies Inc.",
                  supplier_id: "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
                  tax_id: "123-456-789",
                  version: 1,
                },
              ],
              total: 1,
            },
            200,
          ),
        ),
      query: "ACME",
    });

    expect(state).toEqual({
      correlationId: "012f42cd-c035-42ff-987a-5919bd7396aa",
      items: [
        {
          code: "ACME-001",
          defaultCurrency: "PHP",
          isActive: true,
          legalName: "ACME Supplies Inc.",
          supplierId: "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
          taxId: "123-456-789",
          version: 1,
        },
      ],
      kind: "ready",
      total: 1,
    });
  });

  it("returns unauthenticated when no access token is provided", async () => {
    const state = await searchSuppliers({
      accessToken: undefined,
      baseUrl: "https://api.tradeflow.test",
      correlationId: "correlation-id",
    });

    expect(state).toEqual({
      correlationId: "correlation-id",
      kind: "unauthenticated",
    });
  });

  it("returns the created supplier after successful registration", async () => {
    const state = await createSupplier({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      command: supplierInput,
      correlationId: "5c7e5df0-e8f9-4e8e-b950-20483f0aa33f",
      fetch: () =>
        Promise.resolve(
          jsonResponse(
            {
              code: "ACME-001",
              default_currency: "PHP",
              is_active: true,
              legal_name: "ACME Supplies Inc.",
              payment_terms: "Net 30",
              supplier_id: "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
              tax_id: "123-456-789",
              version: 1,
            },
            201,
          ),
        ),
    });

    expect(state.kind).toBe("created");
    if (state.kind === "created") {
      expect(state.supplier.supplierId).toBe(
        "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
      );
      expect(state.supplier.paymentTerms).toBe("Net 30");
    }
  });

  it("maps conflict responses to conflict state", async () => {
    const state = await createSupplier({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      command: supplierInput,
      correlationId: "correlation-id",
      fetch: () =>
        Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "supplier_code_duplicate",
                correlation_id: "correlation-id",
                message: "A supplier with this code already exists.",
              },
            },
            409,
          ),
        ),
    });

    expect(state).toEqual({
      correlationId: "correlation-id",
      kind: "conflict",
    });
  });
});
