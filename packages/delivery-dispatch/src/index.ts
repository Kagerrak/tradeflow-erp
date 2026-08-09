import { createTradeFlowClient, type components } from "@tradeflow/api-client";

export type DispatchCommand = components["schemas"]["DispatchCommand"];

export type DeliveryLine = {
  deliveryLineId: string;
  identityPositions: Array<{
    deliveryLineIdentityAllocationId: string;
    expirationDate: string | null;
    kind: "lot" | "serial" | "untracked";
    lotCode: string | null;
    quantityBase: string;
    serialNumber: string | null;
  }>;
  lineId: string;
  lotSelections: Array<{
    expirationDate: string;
    lotCode: string;
    quantityBase: string;
  }>;
  quantityBase: string;
  serialNumbers: string[];
  skuCode: string;
  skuId: string;
  skuName: string;
};

export type AssignedDelivery = {
  assignedTo: string;
  collectionRequired: boolean;
  collectionAmountDue: string | null;
  deliveryAddress: Record<string, unknown>;
  deliveryId: string;
  evidenceRequirements: string[];
  fulfillmentOrderId: string;
  lines: DeliveryLine[];
  paymentTimingPolicy: "cash_on_delivery" | "on_account" | "prepaid";
  recipientName: string;
  status: "confirmed" | "dispatched";
  version: number;
};

export type DispatchResult = {
  assignedTo: string;
  deliveryId: string;
  fulfillmentOrderId: string;
  lines: Array<{
    lineId: string;
    lotSelections: Array<Record<string, string>>;
    quantityBase: string;
    serialNumbers: string[];
    skuId: string;
    stagingMovementIds: string[];
    transitMovementIds: string[];
  }>;
  paymentTimingPolicy: "cash_on_delivery" | "on_account" | "prepaid";
  status: "dispatched";
  version: number;
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

export type DispatchState =
  | FailureState
  | { correlationId: string; delivery: DispatchResult; kind: "dispatched" };

export type AssignedDeliveryListState =
  | FailureState
  | {
      cacheTag: string | null;
      correlationId: string;
      items: AssignedDelivery[];
      kind: "ready";
      total: number;
    };

type ClientInput = {
  accessToken: string | undefined;
  baseUrl: string;
  correlationId: string;
  fetch?: (request: Request) => Promise<Response>;
};

type ErrorEnvelope = {
  error?: {
    code?: unknown;
    correlation_id?: unknown;
    message?: unknown;
  };
};

type DispatchWire = components["schemas"]["DispatchResponse"];
type AssignedWire = components["schemas"]["AssignedDeliveryResponse"];

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
  const error = ((payload ?? {}) as ErrorEnvelope).error;
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
        : "The Delivery service did not accept this request.",
  };
}

function unavailable(correlationId: string): FailureState {
  return {
    code: "delivery_service_unavailable",
    correlationId,
    kind: "unavailable",
    message: "The Delivery service could not be reached.",
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

function mapAssigned(value: AssignedWire): AssignedDelivery {
  return {
    assignedTo: value.assigned_to,
    collectionRequired: value.collection_required,
    collectionAmountDue: value.collection_amount_due,
    deliveryAddress: value.delivery_address,
    deliveryId: value.delivery_id,
    evidenceRequirements: value.evidence_requirements,
    fulfillmentOrderId: value.fulfillment_order_id,
    lines: value.lines.map((line) => ({
      deliveryLineId:
        (line as typeof line & { delivery_line_id?: string })
          .delivery_line_id ?? line.line_id,
      identityPositions: identityPositions(line),
      lineId: line.line_id,
      lotSelections: line.lot_selections.map((lot) => ({
        expirationDate: lot.expiration_date ?? "",
        lotCode: lot.lot_code ?? "",
        quantityBase: lot.quantity_base ?? "0.000000",
      })),
      quantityBase: line.quantity_base,
      serialNumbers: line.serial_numbers,
      skuCode: line.sku_code,
      skuId: line.sku_id,
      skuName: line.sku_name,
    })),
    paymentTimingPolicy: value.payment_timing_policy,
    recipientName: value.recipient_name,
    status: value.status,
    version: value.version,
  };
}

function identityPositions(
  line: AssignedWire["lines"][number],
): DeliveryLine["identityPositions"] {
  return line.identity_positions.map((position) => ({
    deliveryLineIdentityAllocationId:
      position.delivery_line_identity_allocation_id,
    expirationDate: null,
    kind: position.tracking_policy,
    lotCode: position.lot_code,
    quantityBase: position.quantity_base,
    serialNumber: position.serial_number,
  }));
}

function mapDispatch(value: DispatchWire): DispatchResult {
  return {
    assignedTo: value.assigned_to,
    deliveryId: value.delivery_id,
    fulfillmentOrderId: value.fulfillment_order_id,
    lines: value.lines.map((line) => ({
      lineId: line.line_id,
      lotSelections: line.lot_selections,
      quantityBase: line.quantity_base,
      serialNumbers: line.serial_numbers,
      skuId: line.sku_id,
      stagingMovementIds: line.staging_movement_ids,
      transitMovementIds: line.transit_movement_ids,
    })),
    paymentTimingPolicy: value.payment_timing_policy,
    status: value.status,
    version: value.version,
  };
}

export async function dispatchFulfillment(
  input: ClientInput & {
    command: DispatchCommand;
    fulfillmentOrderId: string;
    idempotencyKey: string;
  },
): Promise<DispatchState> {
  const client = clientFor(input);
  if (client === null) {
    return {
      code: "authentication_required",
      correlationId: input.correlationId,
      kind: "unauthenticated",
      message: "Sign in before dispatching warehouse custody.",
    };
  }
  try {
    const { data, error, response } = await client.POST(
      "/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
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
      delivery: mapDispatch(data),
      kind: "dispatched",
    };
  } catch {
    return unavailable(input.correlationId);
  }
}

export async function listAssignedDeliveries(
  input: ClientInput,
): Promise<AssignedDeliveryListState> {
  const client = clientFor(input);
  if (client === null) {
    return {
      code: "authentication_required",
      correlationId: input.correlationId,
      kind: "unauthenticated",
      message: "Sign in before loading assigned Deliveries.",
    };
  }
  try {
    const { data, error, response } = await client.GET(
      "/v1/deliveries/assigned",
    );
    if (data === undefined) {
      return failureFrom(error, response, input.correlationId);
    }
    return {
      cacheTag: response.headers.get("ETag"),
      correlationId:
        response.headers.get("X-Correlation-ID") ?? input.correlationId,
      items: data.items.map(mapAssigned),
      kind: "ready",
      total: data.total,
    };
  } catch {
    return unavailable(input.correlationId);
  }
}
