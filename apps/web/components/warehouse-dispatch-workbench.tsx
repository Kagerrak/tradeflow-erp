"use client";

import {
  type DispatchCommand,
  type DispatchState,
} from "@tradeflow/delivery-dispatch";
import {
  type PickHistoryItem,
  type PickingContextState,
  type PickListState,
} from "@tradeflow/warehouse-picking";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { PageHeader } from "./ui/page-header";

type LoadState = PickingContextState | { kind: "idle" } | { kind: "loading" };
type CommandState = DispatchState | { kind: "idle" } | { kind: "submitting" };
type PendingDispatch = {
  command: DispatchCommand;
  idempotencyKey: string;
};

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

export function WarehouseDispatchWorkbench({
  initialFulfillmentOrderId,
}: {
  initialFulfillmentOrderId: string;
}) {
  const [fulfillmentOrderId, setFulfillmentOrderId] = useState(
    initialFulfillmentOrderId,
  );
  const [loadState, setLoadState] = useState<LoadState>({ kind: "idle" });
  const [history, setHistory] = useState<PickListState | null>(null);
  const [assignedTo, setAssignedTo] = useState("");
  const [commandState, setCommandState] = useState<CommandState>({
    kind: "idle",
  });
  const pending = useRef<PendingDispatch | null>(null);

  const load = useCallback(async (orderId: string) => {
    if (orderId.trim().length === 0) {
      setLoadState({ kind: "idle" });
      setHistory(null);
      return;
    }
    setLoadState({ kind: "loading" });
    setCommandState({ kind: "idle" });
    try {
      const [contextResponse, picksResponse] = await Promise.all([
        fetch(`/api/picking/${orderId}`, { cache: "no-store" }),
        fetch(`/api/picking/${orderId}/picks`, { cache: "no-store" }),
      ]);
      setLoadState(await readJson<PickingContextState>(contextResponse));
      setHistory(await readJson<PickListState>(picksResponse));
    } catch {
      setLoadState({
        code: "dispatch_service_unavailable",
        correlationId: crypto.randomUUID(),
        kind: "unavailable",
        message: "The dispatch context could not be reached.",
      });
    }
  }, []);

  useEffect(() => {
    if (initialFulfillmentOrderId.length > 0) {
      const timer = window.setTimeout(() => {
        void load(initialFulfillmentOrderId);
      }, 0);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [initialFulfillmentOrderId, load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    void load(fulfillmentOrderId);
  };

  const postDispatch = useCallback(
    async (work: PendingDispatch) => {
      setCommandState({ kind: "submitting" });
      try {
        const response = await fetch(`/api/dispatch/${fulfillmentOrderId}`, {
          body: JSON.stringify(work),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        });
        setCommandState(await readJson<DispatchState>(response));
      } catch {
        setCommandState({
          code: "dispatch_service_unavailable",
          correlationId: crypto.randomUUID(),
          kind: "unavailable",
          message: "The Dispatch outcome is uncertain. Retry unchanged work.",
        });
      }
    },
    [fulfillmentOrderId],
  );

  const dispatch = () => {
    if (loadState.kind !== "ready" || history?.kind !== "ready") return;
    const pickIds = eligiblePicks(history.items).map((pick) => pick.pickId);
    if (assignedTo.trim().length === 0 || pickIds.length === 0) return;
    const work: PendingDispatch = {
      command: {
        assigned_to: assignedTo.trim(),
        delivery_id: crypto.randomUUID(),
        expected_fulfillment_version: loadState.context.version,
        pick_ids: pickIds,
      },
      idempotencyKey: crypto.randomUUID(),
    };
    pending.current = work;
    void postDispatch(work);
  };

  return (
    <>
      <PageHeader
        description="Select immutable staged Picks, bind one authorized driver, and let the server acknowledge the move into In Transit custody."
        eyebrow="Warehouse"
        title="Dispatch"
      />

      <section className="picking-intro card">
        <div>
          <p className="eyebrow">Custody release / 005</p>
          <h1>Release custody, assign the run.</h1>
        </div>
        <p>
          Select immutable staged Picks, bind one authorized driver, and let the
          server acknowledge the move into In Transit custody.
        </p>
      </section>

      <form className="picking-order-search" onSubmit={submitSearch}>
        <label htmlFor="dispatch-order">Fulfillment Order ID</label>
        <div>
          <input
            id="dispatch-order"
            onChange={(event) => setFulfillmentOrderId(event.target.value)}
            value={fulfillmentOrderId}
          />
          <button type="submit">Open dispatch work</button>
        </div>
      </form>

      <DispatchDesk
        assignedTo={assignedTo}
        commandState={commandState}
        history={history}
        loadState={loadState}
        onAssignedTo={setAssignedTo}
        onDispatch={dispatch}
        onRetry={() => {
          if (pending.current !== null) void postDispatch(pending.current);
        }}
      />
    </>
  );
}

function eligiblePicks(items: PickHistoryItem[]): PickHistoryItem[] {
  const reversed = new Set(
    items
      .filter((item) => item.eventType === "reversed")
      .map((item) => item.reversalOfPickId),
  );
  return items.filter(
    (item) =>
      item.eventType === "posted" &&
      !item.dispatched &&
      !reversed.has(item.pickId),
  );
}

function DispatchDesk({
  assignedTo,
  commandState,
  history,
  loadState,
  onAssignedTo,
  onDispatch,
  onRetry,
}: {
  assignedTo: string;
  commandState: CommandState;
  history: PickListState | null;
  loadState: LoadState;
  onAssignedTo: (value: string) => void;
  onDispatch: () => void;
  onRetry: () => void;
}) {
  if (loadState.kind === "idle") {
    return (
      <State
        title="The dispatch rail is clear"
        detail="Open staged Fulfillment work."
      />
    );
  }
  if (loadState.kind === "loading") {
    return (
      <State
        title="Reading staged custody"
        detail="Checking Picks and identities…"
      />
    );
  }
  if (loadState.kind !== "ready") {
    return (
      <State
        title={
          loadState.kind === "forbidden"
            ? "Dispatch authority required"
            : "Dispatch work unavailable"
        }
        detail={`${loadState.message} · ${loadState.code}`}
      />
    );
  }
  if (commandState.kind === "dispatched") {
    return (
      <State
        title="Custody acknowledged in transit"
        detail={
          <>
            Delivery {commandState.delivery.deliveryId} is assigned to{" "}
            <strong>{commandState.delivery.assignedTo}</strong>.
          </>
        }
        tone="positive"
      />
    );
  }
  if (
    commandState.kind === "conflict" ||
    commandState.kind === "forbidden" ||
    commandState.kind === "validation"
  ) {
    return (
      <State
        title={
          commandState.kind === "conflict"
            ? "Authoritative dispatch changed"
            : commandState.kind === "forbidden"
              ? "Dispatch authority required"
              : "Assignment is not eligible"
        }
        detail={`${commandState.message} · ${commandState.code}`}
        tone="critical"
      />
    );
  }
  if (commandState.kind === "unavailable") {
    return (
      <State
        title="Safe retry retained"
        detail={commandState.message}
        action={<button onClick={onRetry}>Retry unchanged dispatch</button>}
      />
    );
  }
  const picks = history?.kind === "ready" ? eligiblePicks(history.items) : [];
  if (loadState.context.status === "dispatched") {
    return (
      <State
        title="Fulfillment custody dispatched"
        detail="All staged Picks are already bound to In Transit Deliveries."
        tone="positive"
      />
    );
  }
  const staged = picks.reduce(
    (sum, pick) => sum + Number(pick.quantityBase),
    0,
  );
  return (
    <section className="dispatch-workspace" aria-live="polite">
      <div className="dispatch-manifest">
        <p className="state-kicker">
          {loadState.context.status === "partially_picked"
            ? "Partially staged"
            : loadState.context.status === "partially_dispatched"
              ? "Partially dispatched"
              : "Ready to dispatch"}
        </p>
        <h2>{picks.length} immutable Pick manifest</h2>
        <p>{staged.toFixed(6)} Base Stocking Units ready for handoff.</p>
        {picks.map((pick) => (
          <article key={pick.pickId}>
            <strong>{pick.pickId}</strong>
            <span>{pick.quantityBase} staged</span>
          </article>
        ))}
      </div>
      <div className="dispatch-handoff">
        <div className="dispatch-custody" aria-label="Dispatch custody rail">
          <strong>Dispatch Staging</strong>
          <span aria-hidden="true">→</span>
          <strong>In Transit</strong>
        </div>
        <label htmlFor="delivery-assignee">Delivery Staff subject</label>
        <input
          id="delivery-assignee"
          onChange={(event) => onAssignedTo(event.target.value)}
          value={assignedTo}
        />
        <button
          disabled={
            assignedTo.trim().length === 0 ||
            picks.length === 0 ||
            commandState.kind === "submitting"
          }
          onClick={onDispatch}
        >
          {commandState.kind === "submitting"
            ? "Posting custody…"
            : "Dispatch selected Pick"}
        </button>
      </div>
    </section>
  );
}

function State({
  action,
  detail,
  title,
  tone = "neutral",
}: {
  action?: ReactNode;
  detail: ReactNode;
  title: string;
  tone?: "critical" | "neutral" | "positive";
}) {
  return (
    <section className={`dispatch-state dispatch-state-${tone}`}>
      <p className="state-kicker">Dispatch ledger</p>
      <h2>{title}</h2>
      <p>{detail}</p>
      {action}
    </section>
  );
}
