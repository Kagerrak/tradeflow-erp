import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type CustomerDirectoryItem = {
  accountNumber: string;
  branchId: string;
  creditHold: boolean;
  customerId: string;
  legalName: string;
  paymentTimingPolicy: "prepaid" | "cash_on_delivery" | "on_account";
  status: "active" | "inactive" | "prospect";
  version: number;
};

export type CustomerDirectoryState =
  | {
      correlationId: string;
      kind: "unauthenticated" | "forbidden" | "validation" | "unavailable";
    }
  | {
      correlationId: string;
      items: CustomerDirectoryItem[];
      kind: "ready";
      total: number;
    };

export type CreateCustomerAccountInput =
  components["schemas"]["CreateCustomerCommand"];

export type CreatedCustomer = {
  accountNumber: string;
  branchId: string;
  creditHold: boolean;
  creditLimit: string | null;
  customerId: string;
  legalName: string;
  paymentTerms: string;
  paymentTimingPolicy: "prepaid" | "cash_on_delivery" | "on_account";
  status: "active" | "inactive" | "prospect";
  version: number;
};

export type CustomerCreationState =
  | {
      correlationId: string;
      kind:
        | "unauthenticated"
        | "forbidden"
        | "validation"
        | "conflict"
        | "unavailable";
    }
  | {
      correlationId: string;
      customer: CreatedCustomer;
      kind: "created";
    };

export type SearchCustomerDirectoryOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
  query: string;
};

export type CreateCustomerAccountOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  command: CreateCustomerAccountInput;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
  idempotencyKey: string;
};

export async function createCustomerAccount({
  accessToken,
  baseUrl,
  command,
  correlationId,
  fetch,
  idempotencyKey,
}: CreateCustomerAccountOptions): Promise<CustomerCreationState> {
  if (accessToken === undefined || accessToken.length === 0) {
    return { correlationId, kind: "unauthenticated" };
  }

  try {
    const client = createTradeFlowClient({
      accessToken,
      baseUrl,
      correlationId,
      ...(fetch === undefined ? {} : { fetch }),
    });
    const { data, response } = await client.POST("/v1/customers", {
      body: command,
      headers: { "Idempotency-Key": idempotencyKey },
    });
    if (response.status === 401) {
      return { correlationId, kind: "unauthenticated" };
    }
    if (response.status === 403) {
      return { correlationId, kind: "forbidden" };
    }
    if (response.status === 409) {
      return { correlationId, kind: "conflict" };
    }
    if (response.status === 422) {
      return { correlationId, kind: "validation" };
    }
    if (response.status >= 500) {
      return { correlationId, kind: "unavailable" };
    }
    if (data === undefined) {
      throw new Error("TradeFlow did not return the created Customer Account.");
    }

    return {
      correlationId,
      customer: {
        accountNumber: data.account_number,
        branchId: data.branch_id,
        creditHold: data.credit_hold,
        creditLimit: data.credit_limit,
        customerId: data.customer_id,
        legalName: data.legal_name,
        paymentTerms: data.payment_terms,
        paymentTimingPolicy: data.payment_timing_policy,
        status: data.status,
        version: data.version,
      },
      kind: "created",
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId, kind: "unavailable" };
    }
    throw error;
  }
}

export async function searchCustomerDirectory({
  accessToken,
  baseUrl,
  correlationId,
  fetch,
  query,
}: SearchCustomerDirectoryOptions): Promise<CustomerDirectoryState> {
  if (accessToken === undefined || accessToken.length === 0) {
    return {
      correlationId,
      kind: "unauthenticated",
    };
  }

  try {
    const client = createTradeFlowClient({
      accessToken,
      baseUrl,
      correlationId,
      ...(fetch === undefined ? {} : { fetch }),
    });
    const { data, response } = await client.GET("/v1/customers", {
      params: { query: { query } },
    });
    if (response.status === 401) {
      return { correlationId, kind: "unauthenticated" };
    }
    if (response.status === 403) {
      return { correlationId, kind: "forbidden" };
    }
    if (response.status === 422) {
      return { correlationId, kind: "validation" };
    }
    if (response.status >= 500) {
      return { correlationId, kind: "unavailable" };
    }
    if (data === undefined) {
      throw new Error("TradeFlow did not return a Customer directory.");
    }

    return {
      correlationId,
      items: data.items.map((item) => ({
        accountNumber: item.account_number,
        branchId: item.branch_id,
        creditHold: item.credit_hold,
        customerId: item.customer_id,
        legalName: item.legal_name,
        paymentTimingPolicy: item.payment_timing_policy,
        status: item.status,
        version: item.version,
      })),
      kind: "ready",
      total: data.total,
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return {
        correlationId,
        kind: "unavailable",
      };
    }
    throw error;
  }
}
