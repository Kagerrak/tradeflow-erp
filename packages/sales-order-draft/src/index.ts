import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type CreateSalesOrderDraftInput =
  components["schemas"]["CreateSalesOrderDraftCommand"];
export type UpdateSalesOrderDraftInput =
  components["schemas"]["UpdateSalesOrderDraftCommand"];

export type OrderEntryReference = {
  addresses: Array<{
    addressKey: string;
    addressVersionId: string;
    city: string;
    line1: string;
    version: number;
  }>;
  branchId: string;
  currency: string;
  customerId: string;
  customerName: string;
  customerVersion: number;
  items: Array<{
    baseQuantityPerUnit: string;
    baseStockingUnit: string;
    floorUnitPrice: string | null;
    unitConversionId: string | null;
    unitConversionVersion: number | null;
    listUnitPrice: string;
    priceListLineId: string;
    skuCode: string;
    skuId: string;
    skuName: string;
    taxCode: string;
    taxRate: string;
    unitCode: string;
  }>;
  paymentTimingDefault: "prepaid" | "cash_on_delivery" | "on_account";
  priceInclusionMode: "inclusive" | "exclusive";
  priceListCode: string;
  priceListVersion: number;
  priceListVersionId: string;
  pricingDate: string;
};

export type SalesOrderDraft = {
  branchId: string;
  currency: string;
  customerId: string;
  customerVersion: number;
  deliveryAddressLine: string;
  discountTotal: string;
  grandTotal: string;
  lines: Array<{
    allocatedDiscount: string;
    enteredQuantity: string;
    enteredUnit: string;
    lineId: string;
    linePosition: number;
    lineTotal: string;
    listUnitPrice: string;
    priceListCode: string;
    priceSource: "customer" | "branch";
    skuCode: string;
    skuName: string;
    taxAmount: string;
  }>;
  paymentTimingOverrideReason: string | null;
  paymentTimingPolicy: "prepaid" | "cash_on_delivery" | "on_account";
  priceInclusionMode: "inclusive" | "exclusive";
  priceListCode: string;
  salesOrderId: string;
  status: "draft";
  subtotal: string;
  taxTotal: string;
  version: number;
};

export type SalesDraftFailureKind =
  "unauthenticated" | "forbidden" | "validation" | "conflict" | "unavailable";

export type ReferenceState =
  | { correlationId: string; kind: SalesDraftFailureKind }
  | { correlationId: string; kind: "ready"; reference: OrderEntryReference };

export type SaveDraftState =
  | { correlationId: string; kind: SalesDraftFailureKind }
  | { correlationId: string; draft: SalesOrderDraft; kind: "saved" };

export type LoadSalesDraftState =
  | { correlationId: string; kind: SalesDraftFailureKind }
  | { correlationId: string; draft: SalesOrderDraft; kind: "loaded" };

export type SalesDraftPreviewLine = {
  allocatedDiscount: string;
  linePosition: number;
  lineTotal: string;
  subtotal: string;
  taxAmount: string;
  taxableAmount: string;
};

export type SalesDraftPreview = {
  discountTotal: string;
  grandTotal: string;
  lines: SalesDraftPreviewLine[];
  subtotal: string;
  taxTotal: string;
  taxableTotal: string;
};

export type SalesDraftPreviewInput = {
  currency: string;
  discountAmount: string;
  inclusionMode: "inclusive" | "exclusive";
  lines: Array<{
    linePosition: number;
    quantity: string;
    taxRate: string;
    unitPrice: string;
  }>;
};

const six = 1_000_000n;

function scaled(value: string, digits: number): bigint {
  const normalized = value.trim();
  const negative = normalized.startsWith("-");
  const unsigned = negative ? normalized.slice(1) : normalized;
  const [whole = "0", fraction = ""] = unsigned.split(".");
  const padded = `${fraction}${"0".repeat(digits)}`.slice(0, digits);
  const result =
    BigInt(whole.length === 0 ? "0" : whole) * 10n ** BigInt(digits) +
    BigInt(padded.length === 0 ? "0" : padded);
  return negative ? -result : result;
}

function roundHalfUp(numerator: bigint, denominator: bigint): bigint {
  if (numerator < 0n) {
    return -roundHalfUp(-numerator, denominator);
  }
  return (numerator * 2n + denominator) / (denominator * 2n);
}

function currencyDigits(currency: string): number {
  if (["CLF", "UYW"].includes(currency)) return 4;
  if (["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"].includes(currency)) {
    return 3;
  }
  if (
    [
      "BIF",
      "CLP",
      "DJF",
      "GNF",
      "ISK",
      "JPY",
      "KMF",
      "KRW",
      "PYG",
      "RWF",
      "UGX",
      "VND",
      "VUV",
      "XAF",
      "XOF",
      "XPF",
    ].includes(currency)
  ) {
    return 0;
  }
  return 2;
}

function moneyMinor(value: string, digits: number): bigint {
  if (digits >= 6) return scaled(value, digits);
  return roundHalfUp(scaled(value, 6), 10n ** BigInt(6 - digits));
}

function formatMinor(value: bigint, digits: number): string {
  const negative = value < 0n;
  const unsigned = negative ? -value : value;
  if (digits === 0) return `${negative ? "-" : ""}${unsigned}`;
  const divisor = 10n ** BigInt(digits);
  return `${negative ? "-" : ""}${unsigned / divisor}.${(unsigned % divisor)
    .toString()
    .padStart(digits, "0")}`;
}

export function previewSalesOrderDraft(
  input: SalesDraftPreviewInput,
): SalesDraftPreview {
  const digits = currencyDigits(input.currency);
  const minorFactor = 10n ** BigInt(digits);
  const rows = input.lines.map((line) => ({
    ...line,
    subtotalMinor: roundHalfUp(
      scaled(line.quantity, 6) * scaled(line.unitPrice, 6) * minorFactor,
      six * six,
    ),
  }));
  const subtotalMinor = rows.reduce(
    (total, line) => total + line.subtotalMinor,
    0n,
  );
  const requestedDiscount = moneyMinor(input.discountAmount, digits);
  const discountMinor =
    requestedDiscount > subtotalMinor ? subtotalMinor : requestedDiscount;
  const allocations = rows.map((line) => {
    if (subtotalMinor === 0n) {
      return { floor: 0n, remainder: 0n };
    }
    const numerator = discountMinor * line.subtotalMinor;
    return {
      floor: numerator / subtotalMinor,
      remainder: numerator % subtotalMinor,
    };
  });
  let residual =
    discountMinor -
    allocations.reduce((total, allocation) => total + allocation.floor, 0n);
  const residualOrder = allocations
    .map((allocation, index) => ({
      index,
      linePosition: rows[index]!.linePosition,
      remainder: allocation.remainder,
    }))
    .sort((left, right) =>
      left.remainder === right.remainder
        ? left.linePosition - right.linePosition
        : left.remainder > right.remainder
          ? -1
          : 1,
    );
  for (const allocation of residualOrder) {
    if (residual === 0n) break;
    allocations[allocation.index]!.floor += 1n;
    residual -= 1n;
  }

  const previewLines = rows.map((line, index): SalesDraftPreviewLine => {
    const allocatedDiscount = allocations[index]!.floor;
    const discounted = line.subtotalMinor - allocatedDiscount;
    const rate = scaled(line.taxRate, 6);
    const taxable =
      input.inclusionMode === "inclusive"
        ? roundHalfUp(discounted * six, six + rate)
        : discounted;
    const tax =
      input.inclusionMode === "inclusive"
        ? discounted - taxable
        : roundHalfUp(taxable * rate, six);
    return {
      allocatedDiscount: formatMinor(allocatedDiscount, digits),
      linePosition: line.linePosition,
      lineTotal: formatMinor(
        input.inclusionMode === "inclusive" ? discounted : taxable + tax,
        digits,
      ),
      subtotal: formatMinor(line.subtotalMinor, digits),
      taxAmount: formatMinor(tax, digits),
      taxableAmount: formatMinor(taxable, digits),
    };
  });
  const taxableTotal = previewLines.reduce(
    (total, line) => total + scaled(line.taxableAmount, digits),
    0n,
  );
  const taxTotal = previewLines.reduce(
    (total, line) => total + scaled(line.taxAmount, digits),
    0n,
  );
  const grandTotal =
    input.inclusionMode === "inclusive"
      ? subtotalMinor - discountMinor
      : taxableTotal + taxTotal;
  return {
    discountTotal: formatMinor(discountMinor, digits),
    grandTotal: formatMinor(grandTotal, digits),
    lines: previewLines,
    subtotal: formatMinor(subtotalMinor, digits),
    taxTotal: formatMinor(taxTotal, digits),
    taxableTotal: formatMinor(taxableTotal, digits),
  };
}

type ClientOptions = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
};

function failureKind(status: number): SalesDraftFailureKind | undefined {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 422) return "validation";
  if (status >= 500) return "unavailable";
  return undefined;
}

function clientFor(options: ClientOptions) {
  return createTradeFlowClient({
    accessToken: options.accessToken ?? "",
    baseUrl: options.baseUrl,
    correlationId: options.correlationId,
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
  });
}

function mapReference(
  value: components["schemas"]["OrderEntryReferenceResponse"],
): OrderEntryReference {
  return {
    addresses: value.addresses.map((address) => ({
      addressKey: address.address_key,
      addressVersionId: address.address_version_id,
      city: address.city,
      line1: address.line_1,
      version: address.version,
    })),
    branchId: value.branch_id,
    currency: value.currency,
    customerId: value.customer_id,
    customerName: value.customer_name,
    customerVersion: value.customer_version,
    items: value.items.map((item) => ({
      baseQuantityPerUnit: item.base_quantity_per_unit,
      baseStockingUnit: item.base_stocking_unit,
      floorUnitPrice: item.floor_unit_price,
      unitConversionId: item.unit_conversion_id,
      unitConversionVersion: item.unit_conversion_version,
      listUnitPrice: item.list_unit_price,
      priceListLineId: item.price_list_line_id,
      skuCode: item.sku_code,
      skuId: item.sku_id,
      skuName: item.sku_name,
      taxCode: item.tax_code,
      taxRate: item.tax_rate,
      unitCode: item.unit_code,
    })),
    paymentTimingDefault: value.payment_timing_default,
    priceInclusionMode: value.price_inclusion_mode,
    priceListCode: value.price_list_code,
    priceListVersion: value.price_list_version,
    priceListVersionId: value.price_list_version_id,
    pricingDate: value.pricing_date,
  };
}

function mapDraft(
  value: components["schemas"]["SalesOrderDraftResponse"],
): SalesOrderDraft {
  return {
    branchId: value.branch_id,
    currency: value.currency,
    customerId: value.customer_id,
    customerVersion: value.customer_version,
    deliveryAddressLine: String(
      value.delivery_address_snapshot["line_1"] ?? "",
    ),
    discountTotal: value.discount_total,
    grandTotal: value.grand_total,
    lines: value.lines.map((line) => ({
      allocatedDiscount: line.allocated_discount,
      enteredQuantity: line.entered_quantity,
      enteredUnit: line.entered_unit,
      lineId: line.line_id,
      linePosition: line.line_position,
      lineTotal: line.line_total,
      listUnitPrice: line.list_unit_price,
      priceListCode: line.price_list_code,
      priceSource: line.price_source,
      skuCode: line.sku_code,
      skuName: line.sku_name,
      taxAmount: line.tax_amount,
    })),
    paymentTimingOverrideReason: value.payment_timing_override_reason,
    paymentTimingPolicy: value.payment_timing_policy,
    priceInclusionMode: value.price_inclusion_mode,
    priceListCode: value.price_list_code,
    salesOrderId: value.sales_order_id,
    status: value.status,
    subtotal: value.subtotal,
    taxTotal: value.tax_total,
    version: value.version,
  };
}

export async function loadOrderEntryReference(
  options: ClientOptions & { branchId: string; customerId: string },
): Promise<ReferenceState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await clientFor(options).GET(
      "/v1/sales/order-entry-reference",
      {
        params: {
          query: {
            branch_id: options.branchId,
            customer_id: options.customerId,
          },
        },
      },
    );
    const failure = failureKind(response.status);
    if (failure !== undefined) {
      return { correlationId: options.correlationId, kind: failure };
    }
    if (data === undefined) throw new Error("Missing Sales Order reference.");
    return {
      correlationId: options.correlationId,
      kind: "ready",
      reference: mapReference(data),
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId: options.correlationId, kind: "unavailable" };
    }
    throw error;
  }
}

export async function createSalesOrderDraft(
  options: ClientOptions & {
    command: CreateSalesOrderDraftInput;
    idempotencyKey: string;
  },
): Promise<SaveDraftState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await clientFor(options).POST(
      "/v1/sales/orders",
      {
        body: options.command,
        params: {
          header: { "Idempotency-Key": options.idempotencyKey },
        },
      },
    );
    const failure = failureKind(response.status);
    if (failure !== undefined) {
      return { correlationId: options.correlationId, kind: failure };
    }
    if (data === undefined) throw new Error("Missing saved Sales Order Draft.");
    return {
      correlationId: options.correlationId,
      draft: mapDraft(data),
      kind: "saved",
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId: options.correlationId, kind: "unavailable" };
    }
    throw error;
  }
}

export async function loadSalesOrderDraft(
  options: ClientOptions & { salesOrderId: string },
): Promise<LoadSalesDraftState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await clientFor(options).GET(
      "/v1/sales/orders/{sales_order_id}",
      {
        params: { path: { sales_order_id: options.salesOrderId } },
      },
    );
    const failure = failureKind(response.status);
    if (failure !== undefined) {
      return { correlationId: options.correlationId, kind: failure };
    }
    if (data === undefined) throw new Error("Missing Sales Order Draft.");
    return {
      correlationId: options.correlationId,
      draft: mapDraft(data),
      kind: "loaded",
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId: options.correlationId, kind: "unavailable" };
    }
    throw error;
  }
}

export async function updateSalesOrderDraft(
  options: ClientOptions & {
    command: UpdateSalesOrderDraftInput;
    expectedVersion: number;
    idempotencyKey: string;
    salesOrderId: string;
  },
): Promise<SaveDraftState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await clientFor(options).PUT(
      "/v1/sales/orders/{sales_order_id}",
      {
        body: options.command,
        params: {
          header: {
            "Idempotency-Key": options.idempotencyKey,
            "If-Match": options.expectedVersion,
          },
          path: { sales_order_id: options.salesOrderId },
        },
      },
    );
    const failure = failureKind(response.status);
    if (failure !== undefined) {
      return { correlationId: options.correlationId, kind: failure };
    }
    if (data === undefined)
      throw new Error("Missing updated Sales Order Draft.");
    return {
      correlationId: options.correlationId,
      draft: mapDraft(data),
      kind: "saved",
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId: options.correlationId, kind: "unavailable" };
    }
    throw error;
  }
}
