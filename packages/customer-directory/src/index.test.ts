import { describe, expect, it } from "vitest";

import {
  createCustomerAccount,
  searchCustomerDirectory,
  type CreateCustomerAccountInput,
} from "./index";

const customerInput: CreateCustomerAccountInput = {
  account_number: "MNL-0001",
  addresses: [
    {
      address_key: "DELIVERY",
      city: "Manila",
      country_code: "PH",
      kind: "delivery",
      line_1: "42 Warehouse Avenue",
      line_2: null,
      postal_code: "1012",
      region: "NCR",
    },
  ],
  branch_id: "a35471fd-27d5-45bc-ac95-4359d766a6d8",
  contacts: [],
  credit_hold: false,
  credit_limit: "0.00",
  legal_name: "North Harbor Stores",
  payment_terms: "DUE_ON_RECEIPT",
  payment_timing_policy: "prepaid",
  status: "active",
};

describe("customer directory journey", () => {
  it("returns only the server-authorized Customer Accounts", async () => {
    const state = await searchCustomerDirectory({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "012f42cd-c035-42ff-987a-5919bd7396aa",
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              items: [
                {
                  account_number: "MNL-0001",
                  branch_id: "a35471fd-27d5-45bc-ac95-4359d766a6d8",
                  credit_hold: false,
                  customer_id: "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
                  legal_name: "North Harbor Stores",
                  payment_timing_policy: "on_account",
                  status: "active",
                  version: 2,
                },
              ],
              total: 1,
            }),
            {
              headers: { "content-type": "application/json" },
              status: 200,
            },
          ),
        ),
      query: "Harbor",
    });

    expect(state).toEqual({
      correlationId: "012f42cd-c035-42ff-987a-5919bd7396aa",
      items: [
        {
          accountNumber: "MNL-0001",
          branchId: "a35471fd-27d5-45bc-ac95-4359d766a6d8",
          creditHold: false,
          customerId: "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
          legalName: "North Harbor Stores",
          paymentTimingPolicy: "on_account",
          status: "active",
          version: 2,
        },
      ],
      kind: "ready",
      total: 1,
    });
  });

  it("returns the server-acknowledged Customer Account after creation", async () => {
    const state = await createCustomerAccount({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      command: customerInput,
      correlationId: "5c7e5df0-e8f9-4e8e-b950-20483f0aa33f",
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              ...customerInput,
              addresses: [
                {
                  ...customerInput.addresses[0],
                  address_version_id: "cb226aec-ff64-4d7b-84ec-caa1a54b4e41",
                  is_current: true,
                  version: 1,
                },
              ],
              contacts: [],
              customer_id: "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
              version: 1,
            }),
            {
              headers: { "content-type": "application/json" },
              status: 201,
            },
          ),
        ),
      idempotencyKey: "create-mnl-0001",
    });

    expect(state.kind).toBe("created");
    if (state.kind === "created") {
      expect(state.customer.customerId).toBe(
        "621124a6-4316-4e3e-b6d4-ec32ea37ce47",
      );
      expect(state.customer.legalName).toBe("North Harbor Stores");
    }
  });

  it("represents an authorized empty result without inventing records", async () => {
    const state = await searchCustomerDirectory({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId: "36ca5e84-20b0-461e-84a1-78fc7d5219ba",
      fetch: () =>
        Promise.resolve(
          new Response(JSON.stringify({ items: [], total: 0 }), {
            headers: { "content-type": "application/json" },
            status: 200,
          }),
        ),
      query: "",
    });

    expect(state).toEqual({
      correlationId: "36ca5e84-20b0-461e-84a1-78fc7d5219ba",
      items: [],
      kind: "ready",
      total: 0,
    });
  });

  it.each([
    [401, "unauthenticated"],
    [403, "forbidden"],
    [422, "validation"],
    [503, "unavailable"],
  ] as const)("maps HTTP %s to the %s search state", async (status, kind) => {
    const correlationId = "550403c2-3c8d-4730-ad81-b0ce8f03f573";
    const state = await searchCustomerDirectory({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      correlationId,
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "test_error",
                correlation_id: correlationId,
                message: "Test error",
              },
            }),
            {
              headers: { "content-type": "application/json" },
              status,
            },
          ),
        ),
      query: "",
    });

    expect(state).toEqual({ correlationId, kind });
  });

  it.each([
    [403, "forbidden"],
    [409, "conflict"],
    [422, "validation"],
    [503, "unavailable"],
  ] as const)("maps HTTP %s to the %s creation state", async (status, kind) => {
    const correlationId = "32545f1f-1472-40c3-b7d2-e1d087525653";
    const state = await createCustomerAccount({
      accessToken: "signed-token",
      baseUrl: "https://api.tradeflow.test",
      command: customerInput,
      correlationId,
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "test_error",
                correlation_id: correlationId,
                message: "Test error",
              },
            }),
            {
              headers: { "content-type": "application/json" },
              status,
            },
          ),
        ),
      idempotencyKey: "create-mnl-0001",
    });

    expect(state).toEqual({ correlationId, kind });
  });
});
