"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type QueueName = "investigation" | "resolved" | "retry" | "return_pending";
type ExceptionItem = {
  age_days: number;
  custody: "in_transit" | "investigation" | "quarantine" | "resolved";
  delivery_id: string;
  delivery_version: number;
  delivery_line_id: string;
  exception_case_id: string;
  exception_kind: "damaged" | "refused" | "short_missing" | "still_undelivered";
  evidence_ids: string[];
  investigation_id: string | null;
  opened_at: string;
  open_quantity_base: string;
  original_quantity_base: string;
  responsible_party_type: string;
  status: string;
  tracking_policy: "lot" | "serial" | "untracked";
  version: number;
};
type Failure = {
  code: string;
  correlationId: string;
  kind:
    "conflict" | "forbidden" | "unauthenticated" | "unavailable" | "validation";
  message: string;
};
type LoadState =
  { kind: "loading" } | Failure | { items: ExceptionItem[]; kind: "ready" };
type ActionState = { kind: "idle" | "pending" | "success" } | Failure;

const queues: Array<[QueueName, string]> = [
  ["return_pending", "Awaiting return"],
  ["investigation", "Investigation"],
  ["retry", "Retry delivery"],
  ["resolved", "Resolved history"],
];

export function DeliveryExceptionWorkspace() {
  const [queue, setQueue] = useState<QueueName>("return_pending");
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selected, setSelected] = useState<ExceptionItem | null>(null);
  const [reason, setReason] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [actionEvidenceIds, setActionEvidenceIds] = useState<string[]>([]);
  const [quantity, setQuantity] = useState("");
  const [resolutionType, setResolutionType] = useState<
    "carrier_claim" | "inventory_adjustment" | "recovery"
  >("recovery");
  const [action, setAction] = useState<ActionState>({ kind: "idle" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    setSelected(null);
    try {
      const response = await fetch(`/api/delivery-exceptions?queue=${queue}`, {
        cache: "no-store",
      });
      const payload = (await response.json()) as
        Failure | { items: ExceptionItem[] };
      setState(
        response.ok
          ? {
              items: filterQueue(
                (payload as { items: ExceptionItem[] }).items,
                queue,
              ),
              kind: "ready",
            }
          : (payload as Failure),
      );
    } catch {
      setState({
        code: "delivery_exception_service_unavailable",
        correlationId: crypto.randomUUID(),
        kind: "unavailable",
        message: "Exception custody could not be reached.",
      });
    }
  }, [queue]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const choose = (item: ExceptionItem) => {
    setSelected(item);
    setQuantity(item.open_quantity_base);
    setReason("");
    setAssignedTo("");
    setActionEvidenceIds(item.evidence_ids);
    setAction({ kind: "idle" });
  };

  const submit = async () => {
    if (selected === null || reason.trim() === "" || quantity.trim() === "")
      return;
    const commandId = crypto.randomUUID();
    setAction({ kind: "pending" });
    const resolution = queue === "investigation";
    const retry = queue === "retry";
    if (retry && assignedTo.trim() === "") return;
    const path = retry
      ? `/api/deliveries/${selected.delivery_id}/retries`
      : resolution
        ? `/api/delivery-investigations/${selected.investigation_id ?? selected.exception_case_id}/resolutions`
        : `/api/deliveries/${selected.delivery_id}/return-to-warehouse-receipts`;
    const body = retry
      ? {
          command: {
            assigned_to: assignedTo.trim(),
            expected_delivery_version: selected.delivery_version,
            reason: reason.trim(),
            retry_delivery_id: commandId,
          },
          idempotencyKey: `delivery-retry:${commandId}`,
        }
      : resolution
        ? {
            command: {
              evidence_ids: actionEvidenceIds,
              expected_investigation_version: selected.version,
              reason: reason.trim(),
              resolution_id: commandId,
              resolution_type: resolutionType,
            },
            idempotencyKey: `investigation-resolution:${commandId}`,
          }
        : {
            command: {
              evidence_ids: actionEvidenceIds,
              expected_delivery_version: selected.delivery_version,
              lines: [
                {
                  damaged_quantity_base:
                    selected.exception_kind === "damaged" ? quantity : "0",
                  delivery_line_id: selected.delivery_line_id,
                  refused_quantity_base:
                    selected.exception_kind === "refused" ? quantity : "0",
                },
              ],
              reason: reason.trim(),
              received_at: new Date().toISOString(),
              return_receipt_id: commandId,
            },
            idempotencyKey: `return-to-warehouse:${commandId}`,
          };
    try {
      const response = await fetch(path, {
        body: JSON.stringify(body),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const payload = (await response.json()) as Failure | object;
      if (!response.ok) {
        setAction(payload as Failure);
        return;
      }
      setAction({ kind: "success" });
      await load();
    } catch {
      setAction({
        code: "delivery_exception_service_unavailable",
        correlationId: commandId,
        kind: "unavailable",
        message: "The outcome is uncertain. Retry unchanged work.",
      });
    }
  };

  return (
    <div className="exception-app">
      <header className="exception-header">
        <Link href="/">TradeFlow</Link>
        <nav aria-label="Exception navigation">
          <Link href="/deliveries">Deliver</Link>
          <strong>Exception custody</strong>
          <Link href="/delivery-corrections">Corrections</Link>
        </nav>
        <span>Inventory control / live</span>
      </header>
      <main className="exception-main">
        <section className="exception-intro">
          <p className="eyebrow">Custody ledger / 007</p>
          <h1>Nothing disappears between stops.</h1>
          <p>
            Receive physical returns into Quarantine, resolve Investigation with
            evidence, and keep retry quantity visible.
          </p>
        </section>
        <nav aria-label="Delivery exception queues" className="exception-tabs">
          {queues.map(([value, label]) => (
            <button
              aria-pressed={queue === value}
              key={value}
              onClick={() => setQueue(value)}
            >
              {label}
            </button>
          ))}
        </nav>
        {state.kind === "loading" && (
          <QueueState title="Reading custody ledger" />
        )}
        {state.kind === "forbidden" && (
          <QueueState
            detail={`${state.message} · ${state.correlationId}`}
            title="Exception queue forbidden"
          />
        )}
        {state.kind === "unauthenticated" && (
          <QueueState
            detail={`${state.message} · ${state.correlationId}`}
            title="Sign in required for exception custody"
          />
        )}
        {(state.kind === "unavailable" ||
          state.kind === "validation" ||
          state.kind === "conflict") && (
          <QueueState
            action="Retry queue"
            detail={`${state.message} · ${state.correlationId}`}
            onAction={() => void load()}
            title="Exception custody unavailable"
          />
        )}
        {state.kind === "ready" && state.items.length === 0 && (
          <QueueState title={emptyTitle(queue)} detail={emptyDetail(queue)} />
        )}
        {state.kind === "ready" && state.items.length > 0 && (
          <div className="exception-workspace">
            <section aria-label="Exception queue" className="exception-ledger">
              <div className="exception-ledger-head">
                <span>Age</span>
                <span>Custody / outcome</span>
                <span>Quantity</span>
              </div>
              {state.items.map((item) => (
                <button
                  key={item.exception_case_id}
                  onClick={() => choose(item)}
                >
                  <strong>{item.age_days}d</strong>
                  <span>
                    <b>Delivery line</b>
                    <small>{item.delivery_line_id}</small>
                    <small>
                      {item.exception_kind.replaceAll("_", " ")} ·{" "}
                      {item.custody.replaceAll("_", " ")}
                    </small>
                  </span>
                  <em>{item.open_quantity_base}</em>
                </button>
              ))}
            </section>
            <ExceptionDetail
              action={action}
              actionEvidenceIds={actionEvidenceIds}
              assignedTo={assignedTo}
              item={selected}
              onSubmit={() => void submit()}
              quantity={quantity}
              queue={queue}
              reason={reason}
              resolutionType={resolutionType}
              setQuantity={setQuantity}
              setReason={setReason}
              setActionEvidenceIds={setActionEvidenceIds}
              setAssignedTo={setAssignedTo}
              setResolutionType={setResolutionType}
            />
          </div>
        )}
      </main>
    </div>
  );
}

function ExceptionDetail({
  action,
  actionEvidenceIds,
  assignedTo,
  item,
  onSubmit,
  quantity,
  queue,
  reason,
  resolutionType,
  setQuantity,
  setReason,
  setActionEvidenceIds,
  setAssignedTo,
  setResolutionType,
}: {
  action: ActionState;
  actionEvidenceIds: string[];
  assignedTo: string;
  item: ExceptionItem | null;
  onSubmit: () => void;
  quantity: string;
  queue: QueueName;
  reason: string;
  resolutionType: "carrier_claim" | "inventory_adjustment" | "recovery";
  setQuantity: (value: string) => void;
  setReason: (value: string) => void;
  setActionEvidenceIds: (value: string[]) => void;
  setAssignedTo: (value: string) => void;
  setResolutionType: (
    value: "carrier_claim" | "inventory_adjustment" | "recovery",
  ) => void;
}) {
  if (item === null)
    return (
      <QueueState
        title="Select a custody record"
        detail="Review evidence and the next authorized movement."
      />
    );
  return (
    <section className="exception-detail">
      <p className="eyebrow">{item.status.toUpperCase()}</p>
      <h2>
        {item.exception_kind.replaceAll("_", " ")} · {item.open_quantity_base}
      </h2>
      <dl>
        <div>
          <dt>Custody</dt>
          <dd>{item.custody.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt>Responsible</dt>
          <dd>{item.responsible_party_type}</dd>
        </div>
        <div>
          <dt>Evidence</dt>
          <dd>{item.evidence_ids.length} retained</dd>
        </div>
        <div>
          <dt>Opened</dt>
          <dd>{item.opened_at}</dd>
        </div>
      </dl>
      {queue === "retry" && (
        <p>
          Retry quantity remains In Transit until a linked Delivery is assigned.
        </p>
      )}
      {(queue === "return_pending" ||
        queue === "investigation" ||
        queue === "retry") && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          {queue === "investigation" && (
            <label>
              Resolution
              <select
                aria-label="Investigation resolution"
                value={resolutionType}
                onChange={(event) =>
                  setResolutionType(event.target.value as typeof resolutionType)
                }
              >
                <option value="recovery">Recovered to Quarantine</option>
                <option value="carrier_claim">Carrier claim</option>
                <option value="inventory_adjustment">
                  Inventory Adjustment
                </option>
              </select>
            </label>
          )}
          {queue === "return_pending" && (
            <label>
              Quantity
              <input
                aria-label="Resolution quantity"
                inputMode="decimal"
                readOnly={item.tracking_policy !== "untracked"}
                value={quantity}
                onChange={(event) => setQuantity(event.target.value)}
              />
            </label>
          )}
          {queue === "retry" && (
            <label>
              Assigned coordinator
              <input
                aria-label="Retry assigned coordinator"
                value={assignedTo}
                onChange={(event) => setAssignedTo(event.target.value)}
              />
            </label>
          )}
          {queue !== "retry" && item.evidence_ids.length > 0 && (
            <fieldset>
              <legend>Action evidence</legend>
              {item.evidence_ids.map((evidenceId) => (
                <label key={evidenceId}>
                  <input
                    checked={actionEvidenceIds.includes(evidenceId)}
                    onChange={(event) =>
                      setActionEvidenceIds(
                        event.target.checked
                          ? [...actionEvidenceIds, evidenceId]
                          : actionEvidenceIds.filter(
                              (value) => value !== evidenceId,
                            ),
                      )
                    }
                    type="checkbox"
                  />
                  {evidenceId}
                </label>
              ))}
            </fieldset>
          )}
          <label>
            Reason
            <textarea
              aria-label="Resolution reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <button
            disabled={
              action.kind === "pending" ||
              action.kind === "unauthenticated" ||
              reason.trim() === "" ||
              (queue === "retry" && assignedTo.trim() === "")
            }
            type="submit"
          >
            {action.kind === "pending"
              ? "Posting immutable entry…"
              : queue === "investigation"
                ? "Resolve Investigation"
                : queue === "retry"
                  ? "Create Retry Delivery"
                  : "Receive into Quarantine"}
          </button>
        </form>
      )}
      {(action.kind === "conflict" ||
        action.kind === "forbidden" ||
        action.kind === "unauthenticated" ||
        action.kind === "validation" ||
        action.kind === "unavailable") && (
        <div className={`exception-action-state ${action.kind}`} role="status">
          <strong>
            {action.kind === "conflict"
              ? "Custody changed — review required"
              : action.kind === "unauthenticated"
                ? "Sign in required before posting"
                : action.kind === "forbidden"
                  ? "Action forbidden"
                  : "Action not posted"}
          </strong>
          <p>
            {action.message} · {action.correlationId}
          </p>
          {action.kind === "conflict" && (
            <small>
              Your reason and quantity remain on screen. Reload before creating
              a new command identity.
            </small>
          )}
        </div>
      )}
    </section>
  );
}

function QueueState({
  action,
  detail,
  onAction,
  title,
}: {
  action?: string;
  detail?: string;
  onAction?: () => void;
  title: string;
}) {
  return (
    <section aria-live="polite" className="exception-state">
      <h2>{title}</h2>
      {detail !== undefined && <p>{detail}</p>}
      {action !== undefined && onAction !== undefined && (
        <button onClick={onAction}>{action}</button>
      )}
    </section>
  );
}

function filterQueue(items: ExceptionItem[], queue: QueueName) {
  if (queue === "resolved") {
    return items.filter((item) => item.status === "resolved");
  }
  const open = items.filter((item) => item.status !== "resolved");
  if (queue === "investigation") {
    return open.filter((item) => item.exception_kind === "short_missing");
  }
  if (queue === "retry") {
    return open.filter((item) => item.exception_kind === "still_undelivered");
  }
  return open.filter(
    (item) =>
      item.exception_kind === "refused" || item.exception_kind === "damaged",
  );
}

function emptyTitle(queue: QueueName) {
  if (queue === "return_pending")
    return "No stock is awaiting warehouse return";
  if (queue === "investigation") return "No custody is under Investigation";
  if (queue === "retry") return "No retry deliveries are waiting";
  return "No resolved exceptions in this scope";
}

function emptyDetail(queue: QueueName) {
  return queue === "return_pending"
    ? "Refused and damaged quantities will appear here until received into Quarantine."
    : "This queue is clear for the current Warehouse scope.";
}
