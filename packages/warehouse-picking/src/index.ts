import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type PickingOperationalState =
  | "loading"
  | "empty"
  | "blocked"
  | "ready"
  | "scan_denied"
  | "manual_fallback"
  | "partial_pick"
  | "conflict"
  | "retry_ready"
  | "forbidden"
  | "reversal"
  | "complete";

export const pickingStateContent: Record<
  PickingOperationalState,
  {
    action: string;
    description: string;
    title: string;
    tone: "attention" | "critical" | "neutral" | "positive";
  }
> = {
  blocked: {
    action: "Resolve the release, payment, or hold gate before scanning.",
    description:
      "This Fulfillment Order exists, but its released quantity is not eligible for warehouse work.",
    title: "Pick release is blocked",
    tone: "attention",
  },
  complete: {
    action: "Handoff the staged goods to Dispatch.",
    description:
      "All released quantity is posted to Dispatch Staging with its identity evidence.",
    title: "Released quantity staged",
    tone: "positive",
  },
  conflict: {
    action: "Refresh the Fulfillment Order and reconcile staged selections.",
    description:
      "Released quantity or physical identity eligibility changed on the server.",
    title: "Authoritative pick changed",
    tone: "critical",
  },
  empty: {
    action: "Refresh when another Fulfillment Order is released.",
    description: "No released quantity is waiting in this Warehouse scope.",
    title: "The pick rail is clear",
    tone: "neutral",
  },
  forbidden: {
    action:
      "Return to scoped work or request the required Warehouse authority.",
    description:
      "This session cannot pick, override, or reverse within the requested Warehouse.",
    title: "Warehouse authority required",
    tone: "critical",
  },
  loading: {
    action: "Wait for the latest released and staged quantities.",
    description:
      "TradeFlow is reading the server-authoritative Fulfillment Order.",
    title: "Reading the pick ledger",
    tone: "neutral",
  },
  manual_fallback: {
    action:
      "Select the identity, record a reason, and repeat every live check.",
    description:
      "Authorized manual selection replaces only barcode capture—not warehouse, tracking, expiration, or FEFO validation.",
    title: "Manual identity selection",
    tone: "attention",
  },
  partial_pick: {
    action: "Continue with the exact remaining released quantity.",
    description:
      "Accepted quantity is in Dispatch Staging; the remainder stays released and reserved.",
    title: "Partial pick posted",
    tone: "positive",
  },
  ready: {
    action: "Scan the identity stack recommended for the next line.",
    description:
      "Released demand and eligible stock are ready for identity assignment.",
    title: "Scanner armed",
    tone: "positive",
  },
  retry_ready: {
    action: "Retry the unchanged command with the same command identity.",
    description:
      "The server outcome is uncertain; changing the command would create different warehouse work.",
    title: "Safe retry retained",
    tone: "attention",
  },
  reversal: {
    action: "Record authority and reason, then refresh released work.",
    description:
      "A linked immutable movement can return an eligible un-dispatched Pick from Dispatch Staging.",
    title: "Reverse staged custody",
    tone: "attention",
  },
  scan_denied: {
    action: "Correct the identity or use authorized manual fallback.",
    description:
      "The scan staged no quantity because its active mapping or stock eligibility failed.",
    title: "Scan denied",
    tone: "critical",
  },
};

export type TrackingPolicy = "lot" | "serial" | "untracked";
export type FulfillmentPickingStatus =
  | "cancelled"
  | "payment_hold"
  | "payment_ready"
  | "partially_picked"
  | "pick_released"
  | "picked"
  | "reserved";

export type FefoCandidate = {
  availableQuantityBase: string;
  expirationDate: string;
  lotCode: string;
  recommended: boolean;
};

export type PickingLine = {
  baseStockingUnit: string;
  expirationControl: boolean;
  fefoCandidates: FefoCandidate[];
  lineId: string;
  pickedQuantityBase: string;
  releasedQuantityBase: string;
  remainingQuantityBase: string;
  reversedQuantityBase: string;
  skuCode: string;
  skuId: string;
  skuName: string;
  trackingPolicy: TrackingPolicy;
};

export type PickingContext = {
  fulfillmentOrderId: string;
  lines: PickingLine[];
  status: FulfillmentPickingStatus;
  version: number;
  warehouseId: string;
};

export type PickSelectionInput = components["schemas"]["PickSelectionInput"];
export type PickLineInput = components["schemas"]["PickLineInput"];
export type PostPickCommand = components["schemas"]["PostPickCommand"];
export type ReversePickCommand = components["schemas"]["ReversePickCommand"];

export type PickLine = {
  conversionSnapshot: {
    baseQuantity: string;
    baseQuantityPerUnit: string;
    enteredQuantity: string;
    enteredUnit: string;
    unitConversionId: string;
  };
  lineId: string;
  lotSelections: Array<{
    expirationDate: string;
    lotCode: string;
    quantityBase: string;
    recommended: boolean;
  }>;
  quantityBase: string;
  serialSelections: string[];
  skuId: string;
  sourceMovementId: string;
  stagingMovementId: string;
};

export type Pick = {
  fulfillmentOrderId: string;
  lines: PickLine[];
  pickId: string;
  pickedQuantityBase: string;
  remainingQuantityBase: string;
  status: "partially_picked" | "picked";
  version: number;
};

export type BarcodeResolution = {
  barcode: string;
  barcodeMappingId: string | null;
  baseQuantityPerUnit: string;
  expirationDate: string | null;
  lotCode: string | null;
  mappingType: "catalog" | "lot_identity" | "serial_identity";
  serialNumber: string | null;
  skuId: string;
  unitCode: string;
};

export type PickReversal = {
  fulfillmentOrderId: string;
  originalPickId: string;
  reversalPickId: string;
  reversedQuantityBase: string;
  sourceMovementIds: string[];
  stagingMovementIds: string[];
  status: "reversed";
  version: number;
};

export type PickHistoryItem = {
  actorSubject: string;
  correlationId: string;
  eventType: "posted" | "reversed";
  lines: PickLine[];
  pickId: string;
  postedAt: string;
  quantityBase: string;
  reason: string | null;
  reversalOfPickId: string | null;
};

export type ClientInput = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
};

export type FailureKind =
  | "conflict"
  | "forbidden"
  | "not_found"
  | "unauthenticated"
  | "unavailable"
  | "validation";

export type FailureState = {
  code: string;
  correlationId: string;
  kind: FailureKind;
  message: string;
};

export type PickingContextState =
  | FailureState
  | {
      context: PickingContext;
      correlationId: string;
      kind: "ready";
    };

export type PostPickState =
  | FailureState
  | {
      correlationId: string;
      kind: "posted";
      pick: Pick;
    };

export type PickReversalState =
  | FailureState
  | {
      correlationId: string;
      kind: "reversed";
      reversal: PickReversal;
    };

export type PickListState =
  | FailureState
  | {
      correlationId: string;
      items: PickHistoryItem[];
      kind: "ready";
      total: number;
    };

export type BarcodeResolutionState =
  | FailureState
  | {
      code: string;
      correlationId: string;
      kind: "scan_denied";
      message: string;
    }
  | {
      correlationId: string;
      kind: "resolved";
      resolution: BarcodeResolution;
    };

type ErrorEnvelope = {
  error?: {
    code?: unknown;
    correlation_id?: unknown;
    message?: unknown;
  };
};

type PickingContextWire = Omit<
  components["schemas"]["PickingContextResponse"],
  "status"
> & { status: FulfillmentPickingStatus };

type PickLineWire = {
  conversion_snapshot: {
    base_quantity: string;
    base_quantity_per_unit: string;
    entered_quantity: string;
    entered_unit: string;
    unit_conversion_id: string;
  };
  line_id: string;
  lot_selections: Array<{
    expiration_date: string;
    lot_code: string;
    quantity_base: string;
    recommended: boolean | string;
  }>;
  quantity_base: string;
  serial_selections: string[];
  sku_id: string;
  source_movement_id: string;
  staging_movement_id: string;
};

type PickWire = Omit<components["schemas"]["PickResponse"], "lines"> & {
  lines: PickLineWire[];
};

type BarcodeResolutionWire = components["schemas"]["BarcodeResolutionResponse"];

type PickReversalWire = components["schemas"]["PickReversalResponse"];

type PickHistoryItemWire = Omit<
  components["schemas"]["PickHistoryItemResponse"],
  "lines"
> & { lines: PickLineWire[] };

function mapPickingContext(value: PickingContextWire): PickingContext {
  return {
    fulfillmentOrderId: value.fulfillment_order_id,
    lines: value.lines.map((line) => ({
      baseStockingUnit: line.base_stocking_unit,
      expirationControl: line.expiration_control,
      fefoCandidates: line.fefo_candidates.map((lot) => ({
        availableQuantityBase: lot.available_quantity_base,
        expirationDate: lot.expiration_date,
        lotCode: lot.lot_code,
        recommended: lot.recommended,
      })),
      lineId: line.line_id,
      pickedQuantityBase: line.picked_quantity_base,
      releasedQuantityBase: line.released_quantity_base,
      remainingQuantityBase: line.remaining_quantity_base,
      reversedQuantityBase: line.reversed_quantity_base,
      skuCode: line.sku_code,
      skuId: line.sku_id,
      skuName: line.sku_name,
      trackingPolicy: line.tracking_policy,
    })),
    status: value.status,
    version: value.version,
    warehouseId: value.warehouse_id,
  };
}

function mapPick(value: PickWire): Pick {
  return {
    fulfillmentOrderId: value.fulfillment_order_id,
    lines: value.lines.map(mapPickLine),
    pickId: value.pick_id,
    pickedQuantityBase: value.picked_quantity_base,
    remainingQuantityBase: value.remaining_quantity_base,
    status: value.status,
    version: value.version,
  };
}

function mapBarcode(value: BarcodeResolutionWire): BarcodeResolution {
  return {
    barcode: value.barcode,
    barcodeMappingId: value.barcode_mapping_id,
    baseQuantityPerUnit: value.base_quantity_per_unit,
    expirationDate: value.expiration_date,
    lotCode: value.lot_code,
    mappingType: value.mapping_type,
    serialNumber: value.serial_number,
    skuId: value.sku_id,
    unitCode: value.unit_code,
  };
}

function mapReversal(value: PickReversalWire): PickReversal {
  return {
    fulfillmentOrderId: value.fulfillment_order_id,
    originalPickId: value.original_pick_id,
    reversalPickId: value.reversal_pick_id,
    reversedQuantityBase: value.reversed_quantity_base,
    sourceMovementIds: value.source_movement_ids,
    stagingMovementIds: value.staging_movement_ids,
    status: value.status,
    version: value.version,
  };
}

function mapPickLine(line: PickLineWire): PickLine {
  return {
    conversionSnapshot: {
      baseQuantity: line.conversion_snapshot.base_quantity,
      baseQuantityPerUnit: line.conversion_snapshot.base_quantity_per_unit,
      enteredQuantity: line.conversion_snapshot.entered_quantity,
      enteredUnit: line.conversion_snapshot.entered_unit,
      unitConversionId: line.conversion_snapshot.unit_conversion_id,
    },
    lineId: line.line_id,
    lotSelections: line.lot_selections.map((selection) => ({
      expirationDate: selection.expiration_date,
      lotCode: selection.lot_code,
      quantityBase: selection.quantity_base,
      recommended:
        selection.recommended === true || selection.recommended === "true",
    })),
    quantityBase: line.quantity_base,
    serialSelections: line.serial_selections,
    skuId: line.sku_id,
    sourceMovementId: line.source_movement_id,
    stagingMovementId: line.staging_movement_id,
  };
}

function mapHistoryItem(value: PickHistoryItemWire): PickHistoryItem {
  return {
    actorSubject: value.actor_subject,
    correlationId: value.correlation_id,
    eventType: value.event_type,
    lines: value.lines.map(mapPickLine),
    pickId: value.pick_id,
    postedAt: value.posted_at,
    quantityBase: value.quantity_base,
    reason: value.reason,
    reversalOfPickId: value.reversal_of_pick_id,
  };
}

function failureKind(status: number): FailureKind {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  return "unavailable";
}

function failureFrom(
  payload: unknown,
  response: Response,
  fallbackCorrelationId: string,
): FailureState {
  const envelope = (payload ?? {}) as ErrorEnvelope;
  const error = envelope.error;
  return {
    code:
      typeof error?.code === "string"
        ? error.code
        : `http_${response.status.toString()}`,
    correlationId:
      typeof error?.correlation_id === "string"
        ? error.correlation_id
        : (response.headers.get("X-Correlation-ID") ?? fallbackCorrelationId),
    kind: failureKind(response.status),
    message:
      typeof error?.message === "string"
        ? error.message
        : "The warehouse service did not accept this request.",
  };
}

function unauthenticated(correlationId: string): FailureState {
  return {
    code: "authentication_required",
    correlationId,
    kind: "unauthenticated",
    message: "Sign in before opening warehouse work.",
  };
}

function unavailable(correlationId: string): FailureState {
  return {
    code: "warehouse_service_unavailable",
    correlationId,
    kind: "unavailable",
    message: "The warehouse service could not be reached.",
  };
}

function clientFor(input: ClientInput) {
  if (input.accessToken === undefined || input.accessToken.length === 0) {
    return null;
  }
  return createTradeFlowClient({
    accessToken: input.accessToken,
    baseUrl: input.baseUrl,
    correlationId: input.correlationId,
    ...(input.fetch === undefined ? {} : { fetch: input.fetch }),
  });
}

export async function getPickingContext(
  input: ClientInput & { fulfillmentOrderId: string },
): Promise<PickingContextState> {
  const client = clientFor(input);
  if (client === null) return unauthenticated(input.correlationId);
  try {
    const { data, error, response } = await client.GET(
      "/v1/fulfillment/orders/{fulfillment_order_id}/picking-context",
      { params: { path: { fulfillment_order_id: input.fulfillmentOrderId } } },
    );
    if (data === undefined) {
      return failureFrom(error, response, input.correlationId);
    }
    return {
      context: mapPickingContext(data as PickingContextWire),
      correlationId:
        response.headers.get("X-Correlation-ID") ?? input.correlationId,
      kind: "ready",
    };
  } catch {
    return unavailable(input.correlationId);
  }
}

export async function postPick(
  input: ClientInput & {
    command: PostPickCommand;
    fulfillmentOrderId: string;
    idempotencyKey: string;
  },
): Promise<PostPickState> {
  const client = clientFor(input);
  if (client === null) return unauthenticated(input.correlationId);
  try {
    const { data, error, response } = await client.POST(
      "/v1/fulfillment/orders/{fulfillment_order_id}/picks",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
        params: { path: { fulfillment_order_id: input.fulfillmentOrderId } },
      },
    );
    if (data === undefined) {
      return failureFrom(error, response, input.correlationId);
    }
    return {
      correlationId:
        response.headers.get("X-Correlation-ID") ?? input.correlationId,
      kind: "posted",
      pick: mapPick(data as PickWire),
    };
  } catch {
    return unavailable(input.correlationId);
  }
}

export async function listPicks(
  input: ClientInput & { fulfillmentOrderId: string },
): Promise<PickListState> {
  const client = clientFor(input);
  if (client === null) return unauthenticated(input.correlationId);
  try {
    const { data, error, response } = await client.GET(
      "/v1/fulfillment/orders/{fulfillment_order_id}/picks",
      { params: { path: { fulfillment_order_id: input.fulfillmentOrderId } } },
    );
    if (data === undefined) {
      return failureFrom(error, response, input.correlationId);
    }
    return {
      correlationId:
        response.headers.get("X-Correlation-ID") ?? input.correlationId,
      items: (data.items as PickHistoryItemWire[]).map(mapHistoryItem),
      kind: "ready",
      total: data.total,
    };
  } catch {
    return unavailable(input.correlationId);
  }
}

export async function reversePick(
  input: ClientInput & {
    command: ReversePickCommand;
    idempotencyKey: string;
    pickId: string;
  },
): Promise<PickReversalState> {
  const client = clientFor(input);
  if (client === null) return unauthenticated(input.correlationId);
  try {
    const { data, error, response } = await client.POST(
      "/v1/fulfillment/picks/{pick_id}/reversal",
      {
        body: input.command,
        headers: { "Idempotency-Key": input.idempotencyKey },
        params: { path: { pick_id: input.pickId } },
      },
    );
    if (data === undefined) {
      return failureFrom(error, response, input.correlationId);
    }
    return {
      correlationId:
        response.headers.get("X-Correlation-ID") ?? input.correlationId,
      kind: "reversed",
      reversal: mapReversal(data as PickReversalWire),
    };
  } catch {
    return unavailable(input.correlationId);
  }
}

export async function resolveBarcode(
  input: ClientInput & {
    barcode: string;
    fulfillmentOrderId: string;
    lineId: string;
    warehouseId: string;
  },
): Promise<BarcodeResolutionState> {
  const client = clientFor(input);
  if (client === null) return unauthenticated(input.correlationId);
  try {
    const { data, error, response } = await client.POST(
      "/v1/inventory/barcodes/resolve",
      {
        body: { barcode: input.barcode, warehouse_id: input.warehouseId },
      },
    );
    if (data === undefined) {
      const failure = failureFrom(error, response, input.correlationId);
      if (
        failure.kind === "validation" ||
        failure.kind === "conflict" ||
        failure.kind === "not_found"
      ) {
        return {
          code: failure.code,
          correlationId: failure.correlationId,
          kind: "scan_denied",
          message: failure.message,
        };
      }
      return failure;
    }
    return {
      correlationId:
        response.headers.get("X-Correlation-ID") ?? input.correlationId,
      kind: "resolved",
      resolution: mapBarcode(data),
    };
  } catch {
    return unavailable(input.correlationId);
  }
}
