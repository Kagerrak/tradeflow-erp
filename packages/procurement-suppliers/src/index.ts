import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type CreateSupplierInput =
  components["schemas"]["CreateSupplierCommand"];

export type SupplierItem = {
  code: string;
  defaultCurrency: string;
  isActive: boolean;
  legalName: string;
  supplierId: string;
  taxId: string | null;
  version: number;
};

export type SupplierSearchState =
  | {
      correlationId: string;
      kind: "unauthenticated" | "forbidden" | "unavailable";
    }
  | {
      correlationId: string;
      items: SupplierItem[];
      kind: "ready";
      total: number;
    };

export type SupplierCreationState =
  | {
      correlationId: string;
      kind:
        | "unauthenticated"
        | "forbidden"
        | "conflict"
        | "validation"
        | "unavailable";
    }
  | {
      correlationId: string;
      kind: "created";
      supplier: SupplierItem & { paymentTerms: string };
    };

export type SearchSuppliersOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
  limit?: number;
  offset?: number;
  query?: string;
};

export type CreateSupplierOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  command: CreateSupplierInput;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
};

function adaptSupplier(
  row: components["schemas"]["SupplierSearchItem"],
): SupplierItem {
  return {
    supplierId: row.supplier_id,
    code: row.code,
    legalName: row.legal_name,
    taxId: row.tax_id ?? null,
    defaultCurrency: row.default_currency,
    isActive: row.is_active,
    version: row.version,
  };
}

function adaptCreatedSupplier(
  row: components["schemas"]["SupplierResponse"],
): SupplierItem & { paymentTerms: string } {
  return {
    supplierId: row.supplier_id,
    code: row.code,
    legalName: row.legal_name,
    taxId: row.tax_id ?? null,
    defaultCurrency: row.default_currency,
    isActive: row.is_active,
    version: row.version,
    paymentTerms: row.payment_terms,
  };
}

export async function searchSuppliers({
  accessToken,
  baseUrl,
  correlationId,
  fetch,
  limit = 25,
  offset = 0,
  query = "",
}: SearchSuppliersOptions): Promise<SupplierSearchState> {
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
    const { data, response } = await client.GET("/v1/procurement/suppliers", {
      params: {
        query: {
          limit,
          offset,
          ...(query.length > 0 ? { query } : {}),
        },
      },
    });
    if (response.status === 401) {
      return { correlationId, kind: "unauthenticated" };
    }
    if (response.status === 403) {
      return { correlationId, kind: "forbidden" };
    }
    if (response.status >= 500) {
      return { correlationId, kind: "unavailable" };
    }
    if (data === undefined) {
      throw new Error("TradeFlow did not return the supplier search result.");
    }
    return {
      correlationId,
      kind: "ready",
      items: data.items.map(adaptSupplier),
      total: data.total,
    };
  } catch {
    return { correlationId, kind: "unavailable" };
  }
}

export async function createSupplier({
  accessToken,
  baseUrl,
  command,
  correlationId,
  fetch,
}: CreateSupplierOptions): Promise<SupplierCreationState> {
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
    const { data, response } = await client.POST("/v1/procurement/suppliers", {
      body: command,
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
      throw new Error("TradeFlow did not return the created supplier.");
    }
    return {
      correlationId,
      kind: "created",
      supplier: adaptCreatedSupplier(data),
    };
  } catch {
    return { correlationId, kind: "unavailable" };
  }
}
