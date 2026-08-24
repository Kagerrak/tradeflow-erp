import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type CreateSalesOrderDraftInput =
  components["schemas"]["CreateSalesOrderDraftCommand"];
export type UpdateSalesOrderDraftInput =
  components["schemas"]["UpdateSalesOrderDraftCommand"];
export type CommercialApprovalInput =
  components["schemas"]["CommercialApprovalCommand"];

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
  status:
    | "draft"
    | "awaiting_approval"
    | "approved"
    | "held"
    | "partially_cancelled"
    | "cancelled";
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

export type CommercialApproval = {
  approvalId: string;
  approvedBy: string;
  backorderQuantityBase: string;
  credit: {
    approvedExcess: string;
    approvedUninvoicedBefore: string;
    creditLimit: string | null;
    openBalance: string;
    orderValue: string;
    overrideRequired: boolean;
    projectedExposure: string;
  };
  makerSubject: string;
  requiredExceptions: string[];
  reservations: Array<{
    backorderQuantityBase: string;
    lineId: string;
    orderedQuantityBase: string;
    reservedQuantityBase: string;
    skuId: string;
  }>;
  reservedQuantityBase: string;
  salesOrderId: string;
  salesOrderRevisionId: string;
  status: "approved";
  warehouseId: string;
};

export type CommercialApprovalState =
  | {
      correlationId: string;
      errorCode?: string;
      kind:
        | "conflict"
        | "exception_required"
        | "forbidden"
        | "held"
        | "unauthenticated"
        | "unavailable"
        | "validation";
      message?: string;
    }
  | {
      approval: CommercialApproval;
      correlationId: string;
      kind: "approved";
    };

export type CommercialReview = {
  approvedUninvoiced: string;
  creditHold: boolean;
  creditLimit: string | null;
  currency: string;
  customerAccountNumber: string;
  customerId: string;
  customerName: string;
  customerSnapshotCurrent: boolean;
  customerStatus: "active" | "inactive" | "prospect";
  discountTotal: string;
  grandTotal: string;
  lines: Array<{
    allocatedDiscount: string;
    backorderQuantityBase: string;
    belowFloor: boolean;
    calculationSnapshot: Record<string, string>;
    conversionSnapshot: Record<string, string>;
    effectiveUnitPrice: string;
    enteredQuantity: string;
    enteredUnit: string;
    floorUnitPrice: string | null;
    lineId: string;
    listUnitPrice: string;
    manualOverrideUnitPrice: string | null;
    quantityBase: string;
    reservableQuantityBase: string;
    skuCode: string;
    skuId: string;
    skuName: string;
    taxSnapshot: Record<string, string>;
    warehouseOnHandBase: string;
    warehouseReservedBase: string;
  }>;
  makerSubject: string;
  openBalance: string;
  paymentTerms: string;
  paymentTimingPolicy: "prepaid" | "cash_on_delivery" | "on_account";
  projectedExposure: string;
  requiredExceptions: Array<{
    amount: string;
    percentage: string | null;
    type: "discount" | "below_floor" | "credit_override";
  }>;
  salesOrderId: string;
  salesOrderRevisionId: string;
  status:
    | "draft"
    | "awaiting_approval"
    | "approved"
    | "held"
    | "partially_cancelled"
    | "cancelled";
  subtotal: string;
  taxTotal: string;
  version: number;
  warehouseId: string;
};

export type CommercialReviewState =
  | {
      correlationId: string;
      kind: SalesDraftFailureKind | "not_found";
    }
  | {
      correlationId: string;
      kind: "ready";
      review: CommercialReview;
    };

export type SalesOrderSearchItem = {
  branchId: string;
  currency: string;
  customerId: string;
  customerName: string;
  grandTotal: string;
  paymentTimingPolicy: "prepaid" | "cash_on_delivery" | "on_account";
  salesOrderId: string;
  status:
    | "draft"
    | "awaiting_approval"
    | "approved"
    | "held"
    | "partially_cancelled"
    | "cancelled";
  version: number;
};

export type SalesOrderSearchState =
  | {
      correlationId: string;
      kind: SalesDraftFailureKind;
    }
  | {
      correlationId: string;
      items: SalesOrderSearchItem[];
      kind: "ready";
      total: number;
    };

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

function mapApproval(
  value: components["schemas"]["CommercialApprovalResponse"],
): CommercialApproval {
  return {
    approvalId: value.commercial_approval_id,
    approvedBy: value.approved_by,
    backorderQuantityBase: value.backorder_quantity_base,
    credit: {
      approvedExcess: value.credit.approved_excess,
      approvedUninvoicedBefore: value.credit.approved_uninvoiced_before,
      creditLimit: value.credit.credit_limit,
      openBalance: value.credit.open_balance,
      orderValue: value.credit.order_value,
      overrideRequired: value.credit.override_required,
      projectedExposure: value.credit.projected_exposure,
    },
    makerSubject: value.maker_subject,
    requiredExceptions: value.required_exceptions,
    reservations: value.reservations.map((line) => ({
      backorderQuantityBase: line.backorder_quantity_base,
      lineId: line.line_id,
      orderedQuantityBase: line.ordered_quantity_base,
      reservedQuantityBase: line.reserved_quantity_base,
      skuId: line.sku_id,
    })),
    reservedQuantityBase: value.reserved_quantity_base,
    salesOrderId: value.sales_order_id,
    salesOrderRevisionId: value.sales_order_revision_id,
    status: value.status,
    warehouseId: value.warehouse_id,
  };
}

type CommercialReviewWire = components["schemas"]["CommercialReviewResponse"];

function mapCommercialReview(value: CommercialReviewWire): CommercialReview {
  return {
    approvedUninvoiced: value.approved_uninvoiced,
    creditHold: value.credit_hold,
    creditLimit: value.credit_limit,
    currency: value.currency,
    customerAccountNumber: value.customer_account_number,
    customerId: value.customer_id,
    customerName: value.customer_name,
    customerSnapshotCurrent: value.customer_snapshot_current,
    customerStatus: value.customer_status,
    discountTotal: value.discount_total,
    grandTotal: value.grand_total,
    lines: value.lines.map((line) => ({
      allocatedDiscount: line.allocated_discount,
      backorderQuantityBase: line.backorder_quantity_base,
      belowFloor: line.below_floor,
      calculationSnapshot: line.calculation_snapshot,
      conversionSnapshot: line.conversion_snapshot,
      effectiveUnitPrice: line.effective_unit_price,
      enteredQuantity: line.entered_quantity,
      enteredUnit: line.entered_unit,
      floorUnitPrice: line.floor_unit_price,
      lineId: line.line_id,
      listUnitPrice: line.list_unit_price,
      manualOverrideUnitPrice: line.manual_override_unit_price,
      quantityBase: line.quantity_base,
      reservableQuantityBase: line.reservable_quantity_base,
      skuCode: line.sku_code,
      skuId: line.sku_id,
      skuName: line.sku_name,
      taxSnapshot: line.tax_snapshot,
      warehouseOnHandBase: line.warehouse_on_hand_base,
      warehouseReservedBase: line.warehouse_reserved_base,
    })),
    makerSubject: value.maker_subject,
    openBalance: value.open_balance,
    paymentTerms: value.payment_terms,
    paymentTimingPolicy: value.payment_timing_policy,
    projectedExposure: value.projected_exposure,
    requiredExceptions: value.required_exceptions.map((exception) => ({
      amount: exception.amount,
      percentage: exception.percentage,
      type: exception.exception_type,
    })),
    salesOrderId: value.sales_order_id,
    salesOrderRevisionId: value.sales_order_revision_id,
    status: value.status,
    subtotal: value.subtotal,
    taxTotal: value.tax_total,
    version: value.version,
    warehouseId: value.warehouse_id,
  };
}

function approvalFailure(
  status: number,
  errorCode: string | undefined,
): Exclude<CommercialApprovalState, { kind: "approved" }>["kind"] | undefined {
  if (errorCode === "customer_credit_hold") return "held";
  if (
    errorCode === "commercial_exception_required" ||
    errorCode === "maker_checker_violation" ||
    errorCode === "approval_authority_required" ||
    errorCode === "approval_limit_exceeded" ||
    errorCode === "exception_reason_required" ||
    errorCode === "credit_override_reason_required"
  ) {
    return "exception_required";
  }
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 422) return "validation";
  if (status >= 500) return "unavailable";
  return undefined;
}

function safeApprovalError(error: unknown): {
  errorCode?: string;
  message?: string;
} {
  if (
    error === undefined ||
    error === null ||
    typeof error !== "object" ||
    !("error" in error) ||
    error.error === null ||
    typeof error.error !== "object"
  ) {
    return {};
  }

  const errorCode =
    "code" in error.error && typeof error.error.code === "string"
      ? error.error.code.trim().slice(0, 100)
      : "";
  const message =
    "message" in error.error && typeof error.error.message === "string"
      ? error.error.message.trim().slice(0, 500)
      : "";

  return {
    ...(errorCode.length > 0 ? { errorCode } : {}),
    ...(message.length > 0 ? { message } : {}),
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

export async function submitSalesOrderDraft(
  options: ClientOptions & {
    expectedVersion: number;
    idempotencyKey: string;
    salesOrderId: string;
  },
): Promise<SaveDraftState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await clientFor(options).POST(
      "/v1/sales/orders/{sales_order_id}/submission",
      {
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
    if (data === undefined) throw new Error("Missing submitted Sales Order.");
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

export async function commerciallyApproveSalesOrder(
  options: ClientOptions & {
    command: CommercialApprovalInput;
    expectedVersion: number;
    idempotencyKey: string;
    salesOrderId: string;
  },
): Promise<CommercialApprovalState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, error, response } = await clientFor(options).POST(
      "/v1/sales/orders/{sales_order_id}/commercial-approval",
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
    const approvalError = safeApprovalError(error);
    const failure = approvalFailure(response.status, approvalError.errorCode);
    if (failure !== undefined) {
      return {
        correlationId: options.correlationId,
        kind: failure,
        ...approvalError,
      };
    }
    if (data === undefined) throw new Error("Missing Commercial Approval.");
    return {
      approval: mapApproval(data),
      correlationId: options.correlationId,
      kind: "approved",
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId: options.correlationId, kind: "unavailable" };
    }
    throw error;
  }
}

export async function loadCommercialReview(
  options: ClientOptions & {
    salesOrderId: string;
    warehouseId: string;
  },
): Promise<CommercialReviewState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await clientFor(options).GET(
      "/v1/sales/orders/{sales_order_id}/commercial-review",
      {
        params: {
          path: { sales_order_id: options.salesOrderId },
          query: { warehouse_id: options.warehouseId },
        },
      },
    );
    if (response.status === 404) {
      return { correlationId: options.correlationId, kind: "not_found" };
    }
    const failure = failureKind(response.status);
    if (failure !== undefined) {
      return { correlationId: options.correlationId, kind: failure };
    }
    if (data === undefined) throw new Error("Missing Commercial Review.");
    return {
      correlationId: options.correlationId,
      kind: "ready",
      review: mapCommercialReview(data),
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId: options.correlationId, kind: "unavailable" };
    }
    throw error;
  }
}

export async function searchSalesOrders(
  options: ClientOptions & { query?: string },
): Promise<SalesOrderSearchState> {
  if (options.accessToken === undefined || options.accessToken.length === 0) {
    return { correlationId: options.correlationId, kind: "unauthenticated" };
  }
  try {
    const { data, response } = await clientFor(options).GET(
      "/v1/sales/orders",
      {
        params: { query: { query: options.query ?? "" } },
      },
    );
    const failure = failureKind(response.status);
    if (failure !== undefined) {
      return { correlationId: options.correlationId, kind: failure };
    }
    if (data === undefined)
      throw new Error("Missing Sales Order search results.");
    return {
      correlationId: options.correlationId,
      items: data.items.map((item) => ({
        branchId: item.branch_id,
        currency: item.currency,
        customerId: item.customer_id,
        customerName: item.customer_name,
        grandTotal: item.grand_total,
        paymentTimingPolicy: item.payment_timing_policy,
        salesOrderId: item.sales_order_id,
        status: item.status,
        version: item.version,
      })),
      kind: "ready",
      total: data.total,
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { correlationId: options.correlationId, kind: "unavailable" };
    }
    throw error;
  }
}
