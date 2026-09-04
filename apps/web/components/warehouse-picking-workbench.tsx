"use client";

import {
  pickingStateContent,
  type BarcodeResolutionState,
  type FailureState,
  type PickHistoryItem,
  type PickingContext,
  type PickingContextState,
  type PickListState,
  type PostPickState,
  type PickReversalState,
} from "@tradeflow/warehouse-picking";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "./ui/page-header";
import { randomId } from "@/lib/random-id";

type LoadState = PickingContextState | { kind: "idle" } | { kind: "loading" };

type CommandState =
  PostPickState | PickReversalState | { kind: "idle" } | { kind: "submitting" };

type ManualSelection = {
  fefoReason: string;
  lotCode: string;
  manualReason: string;
  quantity: string;
  serialNumber: string;
  unitCode: string;
};

const emptyManual: ManualSelection = {
  fefoReason: "",
  lotCode: "",
  manualReason: "",
  quantity: "",
  serialNumber: "",
  unitCode: "",
};

async function readState<T>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

export function WarehousePickingWorkbench({
  initialFulfillmentOrderId,
}: {
  initialFulfillmentOrderId: string;
}) {
  const [orderInput, setOrderInput] = useState(initialFulfillmentOrderId);
  const [activeOrderId, setActiveOrderId] = useState(initialFulfillmentOrderId);
  const [loadState, setLoadState] = useState<LoadState>(
    initialFulfillmentOrderId === "" ? { kind: "idle" } : { kind: "loading" },
  );
  const [history, setHistory] = useState<PickListState | null>(null);
  const [selectedLineId, setSelectedLineId] = useState("");
  const [scan, setScan] = useState("");
  const [scanState, setScanState] = useState<BarcodeResolutionState | null>(
    null,
  );
  const [manualMode, setManualMode] = useState(false);
  const [manual, setManual] = useState<ManualSelection>(emptyManual);
  const [message, setMessage] = useState<string | null>(null);
  const [commandState, setCommandState] = useState<CommandState>({
    kind: "idle",
  });
  const [reversalReason, setReversalReason] = useState("");
  const pickIdentity = useRef<{
    fingerprint: string;
    idempotencyKey: string;
    pickId: string;
  } | null>(null);
  const reversalIdentity = useRef<{
    fingerprint: string;
    idempotencyKey: string;
    reversalId: string;
  } | null>(null);

  const load = useCallback(async (fulfillmentOrderId: string) => {
    if (fulfillmentOrderId.trim() === "") {
      setLoadState({ kind: "idle" });
      setHistory(null);
      return;
    }
    setLoadState({ kind: "loading" });
    setCommandState({ kind: "idle" });
    setScanState(null);
    setManualMode(false);
    try {
      const [contextResponse, picksResponse] = await Promise.all([
        fetch(`/api/picking/${encodeURIComponent(fulfillmentOrderId)}`, {
          cache: "no-store",
        }),
        fetch(`/api/picking/${encodeURIComponent(fulfillmentOrderId)}/picks`, {
          cache: "no-store",
        }),
      ]);
      const nextContext = await readState<PickingContextState>(contextResponse);
      setLoadState(nextContext);
      if (nextContext.kind === "ready") {
        setSelectedLineId(nextContext.context.lines[0]?.lineId ?? "");
      }
      setHistory(await readState<PickListState>(picksResponse));
    } catch {
      setLoadState({
        code: "warehouse_service_unavailable",
        correlationId: randomId(),
        kind: "unavailable",
        message: "The warehouse service could not be reached.",
      });
    }
  }, []);

  useEffect(() => {
    if (initialFulfillmentOrderId === "") return;
    const timer = window.setTimeout(() => {
      void load(initialFulfillmentOrderId);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [initialFulfillmentOrderId, load]);

  const openOrder = (event: FormEvent) => {
    event.preventDefault();
    const next = orderInput.trim();
    setActiveOrderId(next);
    void load(next);
  };

  const context = loadState.kind === "ready" ? loadState.context : undefined;
  const selectedLine =
    context?.lines.find((line) => line.lineId === selectedLineId) ??
    context?.lines[0];

  const resolveScan = async () => {
    if (context === undefined || selectedLine === undefined) return;
    if (scan.trim() === "") {
      setMessage("Scan or enter a barcode before resolving identity.");
      return;
    }
    setMessage(null);
    try {
      const response = await fetch("/api/picking/barcodes/resolve", {
        body: JSON.stringify({
          barcode: scan.trim(),
          fulfillmentOrderId: context.fulfillmentOrderId,
          lineId: selectedLine.lineId,
          warehouseId: context.warehouseId,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const next = await readState<BarcodeResolutionState>(response);
      setScanState(next);
      if (next.kind === "resolved") {
        setManualMode(false);
        setManual({
          fefoReason: "",
          lotCode: next.resolution.lotCode ?? "",
          manualReason: "",
          quantity: "1",
          serialNumber: next.resolution.serialNumber ?? "",
          unitCode: next.resolution.unitCode,
        });
      }
    } catch {
      setScanState({
        code: "warehouse_service_unavailable",
        correlationId: randomId(),
        kind: "unavailable",
        message: "Barcode resolution could not reach the warehouse service.",
      });
    }
  };

  const postCurrentPick = async () => {
    if (context === undefined || selectedLine === undefined) return;
    if (manual.quantity.trim() === "" || manual.unitCode.trim() === "") {
      setMessage("Pick quantity and approved Unit of Measure are required.");
      return;
    }
    if (
      manualMode &&
      (manual.manualReason.trim() === "" ||
        (selectedLine.trackingPolicy === "lot" && manual.lotCode.trim() === ""))
    ) {
      setMessage("Manual identity selection requires identity and reason.");
      return;
    }
    const selectedCandidate = selectedLine.fefoCandidates.find(
      (candidate) => candidate.lotCode === manual.lotCode.trim(),
    );
    if (
      manualMode &&
      selectedCandidate !== undefined &&
      !selectedCandidate.recommended &&
      manual.fefoReason.trim() === ""
    ) {
      setMessage("A later-expiring lot requires a FEFO override reason.");
      return;
    }
    const selection = {
      ...(manualMode
        ? { manual_reason: manual.manualReason.trim() }
        : { barcode: scan.trim() }),
      ...(manual.fefoReason.trim() === ""
        ? {}
        : { fefo_override_reason: manual.fefoReason.trim() }),
      ...(manual.lotCode.trim() === ""
        ? {}
        : { lot_code: manual.lotCode.trim() }),
      quantity: manual.quantity.trim(),
      ...(manual.serialNumber.trim() === ""
        ? {}
        : { serial_number: manual.serialNumber.trim() }),
    };
    const lines = [
      {
        line_id: selectedLine.lineId,
        quantity: manual.quantity.trim(),
        selections:
          selectedLine.trackingPolicy === "untracked" ? [] : [selection],
        unit_code: manual.unitCode.trim().toUpperCase(),
      },
    ];
    const fingerprint = JSON.stringify({
      expected_fulfillment_version: context.version,
      lines,
    });
    if (pickIdentity.current?.fingerprint !== fingerprint) {
      pickIdentity.current = {
        fingerprint,
        idempotencyKey: randomId(),
        pickId: randomId(),
      };
    }
    setMessage(null);
    setCommandState({ kind: "submitting" });
    try {
      const response = await fetch(
        `/api/picking/${encodeURIComponent(context.fulfillmentOrderId)}`,
        {
          body: JSON.stringify({
            command: {
              expected_fulfillment_version: context.version,
              lines,
              pick_id: pickIdentity.current.pickId,
            },
            idempotencyKey: pickIdentity.current.idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      setCommandState(await readState<PostPickState>(response));
    } catch {
      setCommandState({
        code: "warehouse_service_unavailable",
        correlationId: randomId(),
        kind: "unavailable",
        message: "The Pick outcome is uncertain. Retry the unchanged command.",
      });
    }
  };

  const reverse = async (pick: PickHistoryItem) => {
    if (reversalReason.trim() === "") {
      setMessage("Pick reversal requires an operational reason.");
      return;
    }
    const fingerprint = JSON.stringify({
      expected_fulfillment_version: context?.version,
      pickId: pick.pickId,
      reason: reversalReason.trim(),
    });
    if (reversalIdentity.current?.fingerprint !== fingerprint) {
      reversalIdentity.current = {
        fingerprint,
        idempotencyKey: randomId(),
        reversalId: randomId(),
      };
    }
    setMessage(null);
    setCommandState({ kind: "submitting" });
    try {
      const response = await fetch(
        `/api/picking/picks/${encodeURIComponent(pick.pickId)}/reversal`,
        {
          body: JSON.stringify({
            command: {
              expected_fulfillment_version: context?.version ?? 1,
              reason: reversalReason.trim(),
              reversal_pick_id: reversalIdentity.current.reversalId,
            },
            idempotencyKey: reversalIdentity.current.idempotencyKey,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      setCommandState(await readState<PickReversalState>(response));
    } catch {
      setCommandState({
        code: "warehouse_service_unavailable",
        correlationId: randomId(),
        kind: "unavailable",
        message:
          "The reversal outcome is uncertain. Retry the unchanged command.",
      });
    }
  };

  return (
    <>
      <PageHeader
        description="One released commitment. Every identity and quantity follows an immutable path into Dispatch Staging."
        eyebrow="Warehouse"
        title="Picking"
      />

      <section className="picking-intro card">
        <div>
          <p className="eyebrow">Warehouse ledger / 008</p>
          <h1>Move proof, not just product.</h1>
        </div>
        <p>
          One Warehouse. One released commitment. Every identity and quantity
          follows an immutable path into Dispatch Staging.
        </p>
      </section>

      <form className="picking-order-search" onSubmit={openOrder}>
        <label htmlFor="fulfillment-order-id">Fulfillment Order ID</label>
        <div>
          <input
            id="fulfillment-order-id"
            onChange={(event) => setOrderInput(event.target.value)}
            placeholder="Open released warehouse work"
            value={orderInput}
          />
          <button type="submit">Open pick work</button>
        </div>
      </form>

      <div className="picking-workspace">
        <aside className="picking-queue" aria-labelledby="queue-title">
          <p className="picking-section-number">01 / Supervisor queue</p>
          <h2 id="queue-title">Released work</h2>
          {context === undefined ? (
            <p className="picking-muted">
              Open a Fulfillment Order to inspect its Warehouse custody.
            </p>
          ) : (
            <button className="picking-queue-item" type="button">
              <span>{context.status.replaceAll("_", " ")}</span>
              <strong>{context.fulfillmentOrderId}</strong>
              <small>Warehouse {context.warehouseId}</small>
            </button>
          )}
          <HistorySummary history={history} />
        </aside>

        <section className="picking-desk" aria-live="polite">
          <WorkbenchState
            activeOrderId={activeOrderId}
            commandState={commandState}
            context={context}
            history={history}
            loadState={loadState}
            manual={manual}
            manualMode={manualMode}
            message={message}
            onManualChange={setManual}
            onManualMode={() => {
              setManualMode(true);
              setScanState(null);
              if (selectedLine !== undefined) {
                setManual({
                  ...emptyManual,
                  unitCode: selectedLine.baseStockingUnit,
                });
              }
            }}
            onPost={() => void postCurrentPick()}
            onResolve={() => void resolveScan()}
            onRetry={() => void load(activeOrderId)}
            onReverse={(pick) => void reverse(pick)}
            onSelectLine={setSelectedLineId}
            reversalReason={reversalReason}
            scan={scan}
            scanState={scanState}
            selectedLineId={selectedLineId}
            setReversalReason={setReversalReason}
            setScan={setScan}
          />
        </section>
      </div>
    </>
  );
}

function HistorySummary({ history }: { history: PickListState | null }) {
  if (history === null) return null;
  if (history.kind !== "ready") {
    return <p className="picking-muted">Pick history is unavailable.</p>;
  }
  return (
    <div className="picking-history-count">
      <span>Immutable postings</span>
      <strong>{history.total.toString().padStart(2, "0")}</strong>
    </div>
  );
}

type WorkbenchProps = {
  activeOrderId: string;
  commandState: CommandState;
  context: PickingContext | undefined;
  history: PickListState | null;
  loadState: LoadState;
  manual: ManualSelection;
  manualMode: boolean;
  message: string | null;
  onManualChange: (value: ManualSelection) => void;
  onManualMode: () => void;
  onPost: () => void;
  onResolve: () => void;
  onRetry: () => void;
  onReverse: (pick: PickHistoryItem) => void;
  onSelectLine: (lineId: string) => void;
  reversalReason: string;
  scan: string;
  scanState: BarcodeResolutionState | null;
  selectedLineId: string;
  setReversalReason: (value: string) => void;
  setScan: (value: string) => void;
};

function WorkbenchState(props: WorkbenchProps) {
  if (props.loadState.kind === "loading") {
    return <OperationalPanel state="loading" />;
  }
  if (props.loadState.kind === "idle") {
    return <OperationalPanel state="empty" />;
  }
  if (props.loadState.kind !== "ready") {
    return (
      <FailurePanel
        failure={props.loadState}
        {...(props.loadState.kind === "unavailable"
          ? { onRetry: props.onRetry }
          : {})}
      />
    );
  }
  const context = props.context;
  if (context === undefined) {
    return <OperationalPanel state="empty" />;
  }
  if (
    !["pick_released", "partially_picked", "picked"].includes(context.status)
  ) {
    return <OperationalPanel state="blocked" />;
  }
  if (context.lines.length === 0) {
    return <OperationalPanel state="empty" />;
  }
  const line =
    context.lines.find((item) => item.lineId === props.selectedLineId) ??
    context.lines[0]!;
  const commandFailure =
    props.commandState.kind !== "idle" &&
    props.commandState.kind !== "submitting" &&
    props.commandState.kind !== "posted" &&
    props.commandState.kind !== "reversed"
      ? props.commandState
      : undefined;
  const partialPick =
    props.commandState.kind === "posted" &&
    props.commandState.pick.status === "partially_picked"
      ? props.commandState.pick
      : undefined;
  const completePick =
    props.commandState.kind === "posted" &&
    props.commandState.pick.status === "picked"
      ? props.commandState.pick
      : undefined;

  return (
    <>
      <CustodyRail
        identity={
          props.manual.lotCode ||
          props.manual.serialNumber ||
          (line.trackingPolicy === "untracked" ? "Untracked quantity" : "—")
        }
        picked={
          partialPick?.pickedQuantityBase ??
          completePick?.pickedQuantityBase ??
          line.pickedQuantityBase
        }
        released={line.releasedQuantityBase}
        remaining={
          partialPick?.remainingQuantityBase ??
          completePick?.remainingQuantityBase ??
          line.remainingQuantityBase
        }
      />

      {partialPick !== undefined && (
        <OperationalPanel
          detail={`${partialPick.remainingQuantityBase} ${line.baseStockingUnit} remains released.`}
          state="partial_pick"
        />
      )}
      {(completePick !== undefined || context.status === "picked") && (
        <OperationalPanel state="complete" />
      )}
      {props.commandState.kind === "reversed" && (
        <OperationalPanel
          detail={`${props.commandState.reversal.reversedQuantityBase} returned through linked movements.`}
          state="reversal"
        />
      )}
      {commandFailure !== undefined && (
        <FailurePanel
          failure={commandFailure}
          {...(commandFailure.kind === "unavailable"
            ? { onRetry: props.onPost }
            : {})}
        />
      )}

      {context.status !== "picked" && completePick === undefined && (
        <div className="picking-action-grid">
          <section className="picking-manifest">
            <p className="picking-section-number">02 / Active line</p>
            {context.lines.length > 1 && (
              <div className="picking-line-tabs" aria-label="Fulfillment lines">
                {context.lines.map((item, index) => (
                  <button
                    aria-label={`Select ${item.skuCode}`}
                    className={
                      item.lineId === line.lineId
                        ? "line-tab line-tab-active"
                        : "line-tab"
                    }
                    key={item.lineId}
                    onClick={() => props.onSelectLine(item.lineId)}
                    type="button"
                  >
                    {(index + 1).toString().padStart(2, "0")} / {item.skuCode}
                  </button>
                ))}
              </div>
            )}
            <div className="picking-line-title">
              <div>
                <span>{line.skuCode}</span>
                <h2>{line.skuName}</h2>
              </div>
              <strong>{line.trackingPolicy}</strong>
            </div>
            <dl className="picking-line-numbers">
              <div>
                <dt>Released</dt>
                <dd>{line.releasedQuantityBase}</dd>
              </div>
              <div>
                <dt>Picked</dt>
                <dd>{line.pickedQuantityBase}</dd>
              </div>
              <div>
                <dt>Remaining</dt>
                <dd>{line.remainingQuantityBase}</dd>
              </div>
            </dl>
            {line.fefoCandidates.length > 0 && (
              <div className="picking-fefo">
                <span>FEFO identity stack</span>
                {line.fefoCandidates.map((candidate) => (
                  <button
                    className={
                      candidate.recommended
                        ? "fefo-row fefo-recommended"
                        : "fefo-row"
                    }
                    key={candidate.lotCode}
                    onClick={() =>
                      props.onManualChange({
                        ...props.manual,
                        lotCode: candidate.lotCode,
                      })
                    }
                    type="button"
                  >
                    <b>{candidate.lotCode}</b>
                    <span>{candidate.expirationDate}</span>
                    <span>{candidate.availableQuantityBase}</span>
                    <small>
                      {candidate.recommended ? "Pick first" : "Override"}
                    </small>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="picking-capture">
            <p className="picking-section-number">03 / Identity capture</p>
            {!props.manualMode && (
              <>
                <label htmlFor="barcode-scan">Barcode scan</label>
                <div className="picking-scan-row">
                  <input
                    autoFocus
                    id="barcode-scan"
                    onChange={(event) => props.setScan(event.target.value)}
                    placeholder="Scan SKU, unit, lot, or serial"
                    value={props.scan}
                  />
                  <button onClick={props.onResolve} type="button">
                    Resolve barcode
                  </button>
                </div>
              </>
            )}

            {props.scanState?.kind === "scan_denied" && (
              <div className="picking-denial" role="alert">
                <span>DENIED / NO QUANTITY STAGED</span>
                <h2>Scan denied</h2>
                <p>{props.scanState.message}</p>
                <code>{props.scanState.code}</code>
                <button onClick={props.onManualMode} type="button">
                  Use authorized manual selection
                </button>
              </div>
            )}
            {props.scanState !== null &&
              props.scanState.kind !== "resolved" &&
              props.scanState.kind !== "scan_denied" && (
                <FailurePanel failure={props.scanState} />
              )}
            {(props.manualMode || props.scanState?.kind === "resolved") && (
              <ManualForm
                manual={props.manual}
                onChange={props.onManualChange}
                showReasons={props.manualMode}
                trackingPolicy={line.trackingPolicy}
              />
            )}
            {props.message !== null && (
              <p className="picking-form-error" role="alert">
                {props.message}
              </p>
            )}
            {(props.manualMode || props.scanState?.kind === "resolved") && (
              <button
                className="picking-post"
                disabled={props.commandState.kind === "submitting"}
                onClick={props.onPost}
                type="button"
              >
                {props.commandState.kind === "submitting"
                  ? "Posting custody…"
                  : "Post partial pick"}
              </button>
            )}
          </section>
        </div>
      )}

      <PickHistory
        history={props.history}
        onReverse={props.onReverse}
        reversalReason={props.reversalReason}
        setReversalReason={props.setReversalReason}
      />
    </>
  );
}

function ManualForm({
  manual,
  onChange,
  showReasons,
  trackingPolicy,
}: {
  manual: ManualSelection;
  onChange: (value: ManualSelection) => void;
  showReasons: boolean;
  trackingPolicy: PickingContext["lines"][number]["trackingPolicy"];
}) {
  return (
    <div className="picking-manual">
      <p>
        {showReasons
          ? "Manual fallback repeats all identity controls."
          : "Resolved identity ready for exact quantity."}
      </p>
      {trackingPolicy === "lot" && (
        <label>
          Lot code
          <input
            aria-label="Lot code"
            onChange={(event) =>
              onChange({ ...manual, lotCode: event.target.value })
            }
            value={manual.lotCode}
          />
        </label>
      )}
      {trackingPolicy === "serial" && (
        <label>
          Serial number
          <input
            aria-label="Serial number"
            onChange={(event) =>
              onChange({ ...manual, serialNumber: event.target.value })
            }
            value={manual.serialNumber}
          />
        </label>
      )}
      <div className="picking-field-pair">
        <label>
          Pick quantity
          <input
            aria-label="Pick quantity"
            inputMode="decimal"
            onChange={(event) =>
              onChange({ ...manual, quantity: event.target.value })
            }
            value={manual.quantity}
          />
        </label>
        <label>
          Approved unit
          <input
            aria-label="Approved Unit of Measure"
            onChange={(event) =>
              onChange({ ...manual, unitCode: event.target.value })
            }
            value={manual.unitCode}
          />
        </label>
      </div>
      {showReasons && (
        <>
          <label>
            Manual selection reason
            <textarea
              aria-label="Manual selection reason"
              onChange={(event) =>
                onChange({ ...manual, manualReason: event.target.value })
              }
              value={manual.manualReason}
            />
          </label>
          {trackingPolicy === "lot" && (
            <label>
              FEFO override reason
              <textarea
                aria-label="FEFO override reason"
                onChange={(event) =>
                  onChange({ ...manual, fefoReason: event.target.value })
                }
                placeholder="Required only for a later-expiring lot"
                value={manual.fefoReason}
              />
            </label>
          )}
        </>
      )}
    </div>
  );
}

function CustodyRail({
  identity,
  picked,
  released,
  remaining,
}: {
  identity: string;
  picked: string;
  released: string;
  remaining: string;
}) {
  return (
    <section className="custody-rail" aria-label="Pick custody rail">
      <div>
        <span>AVAILABLE</span>
        <strong>Available</strong>
        <small>{released} released</small>
      </div>
      <i aria-hidden="true">→</i>
      <div>
        <span>TRACEABILITY</span>
        <strong>Identity stack</strong>
        <small>{identity}</small>
      </div>
      <i aria-hidden="true">→</i>
      <div>
        <span>WAREHOUSE ON-HAND</span>
        <strong>Dispatch Staging</strong>
        <small>{picked} moved</small>
      </div>
      <p>{remaining} remains released</p>
    </section>
  );
}

function OperationalPanel({
  detail,
  state,
}: {
  detail?: string;
  state:
    "blocked" | "complete" | "empty" | "loading" | "partial_pick" | "reversal";
}) {
  const content = pickingStateContent[state];
  return (
    <section className={`picking-state picking-state-${content.tone}`}>
      <span>{state.replaceAll("_", " ").toUpperCase()}</span>
      <h2>{content.title}</h2>
      <p>{detail ?? content.description}</p>
      <strong>{content.action}</strong>
    </section>
  );
}

function FailurePanel({
  failure,
  onRetry,
}: {
  failure: FailureState;
  onRetry?: () => void;
}) {
  const presentation =
    failure.kind === "forbidden"
      ? pickingStateContent.forbidden
      : failure.kind === "conflict"
        ? pickingStateContent.conflict
        : pickingStateContent.retry_ready;
  return (
    <section className="picking-state picking-state-critical" role="alert">
      <span>{failure.kind.replaceAll("_", " ").toUpperCase()}</span>
      <h2>{presentation.title}</h2>
      <p>{failure.message}</p>
      <code>{failure.code}</code>
      <small>Support reference {failure.correlationId}</small>
      {onRetry !== undefined && (
        <button onClick={onRetry} type="button">
          Retry unchanged work
        </button>
      )}
    </section>
  );
}

function PickHistory({
  history,
  onReverse,
  reversalReason,
  setReversalReason,
}: {
  history: PickListState | null;
  onReverse: (pick: PickHistoryItem) => void;
  reversalReason: string;
  setReversalReason: (value: string) => void;
}) {
  if (history === null || history.kind !== "ready" || history.total === 0) {
    return null;
  }
  return (
    <section className="picking-history">
      <p className="picking-section-number">04 / Immutable pick postings</p>
      <h2>Staging evidence</h2>
      {history.items.map((pick) => (
        <article key={pick.pickId}>
          <div>
            <span>{pick.eventType.replaceAll("_", " ")}</span>
            <strong>{pick.pickId}</strong>
            <small>{pick.quantityBase} moved through custody</small>
          </div>
          {pick.eventType === "posted" ? (
            <>
              <label>
                Reversal reason
                <input
                  onChange={(event) => setReversalReason(event.target.value)}
                  value={reversalReason}
                />
              </label>
              <button onClick={() => onReverse(pick)} type="button">
                Reverse staged pick
              </button>
            </>
          ) : (
            <p className="picking-muted">
              Linked to {pick.reversalOfPickId ?? "original pick"}
            </p>
          )}
        </article>
      ))}
    </section>
  );
}
