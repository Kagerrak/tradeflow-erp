"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { type components } from "@tradeflow/api-client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { PageHeader } from "./ui/page-header";

type ApiCorrectionDetail = components["schemas"]["DeliveryCorrectionResponse"];
type ApiCorrectionSummary = components["schemas"]["DeliveryCorrectionSummary"];
type ApiReceiptDetail = components["schemas"]["DeliveryReceiptDetailResponse"];
type SignedAccessResponse = components["schemas"]["SignedAccessResponse"];

type Status = ApiCorrectionSummary["status"];
type Queue = Status | "request";
type Failure = {
  code: string;
  correlationId: string;
  kind:
    "conflict" | "forbidden" | "unauthenticated" | "unavailable" | "validation";
  message: string;
};
type IdentityPosition = {
  accepted_quantity_base: string;
  damaged_quantity_base: string;
  delivery_line_identity_allocation_id: string;
  expiration_date: string | null;
  lot_code: string | null;
  quantity_base: string;
  refused_quantity_base: string;
  serial_number: string | null;
  short_missing_quantity_base: string;
  still_undelivered_quantity_base: string;
  tracking_policy: "lot" | "serial";
};
type CorrectionLine = {
  accepted_quantity_base: string;
  damaged_quantity_base: string;
  delivery_line_id: string;
  identity_positions: IdentityPosition[];
  refused_quantity_base: string;
  short_missing_quantity_base: string;
  still_undelivered_quantity_base: string;
  line_id?: string;
  sku_id?: string;
  unit_cost?: string;
  value_delta?: string;
};
type CorrectionSummary = ApiCorrectionSummary;
type CorrectionDetail = Omit<ApiCorrectionDetail, "lines"> & {
  lines: CorrectionLine[];
};
type ReceiptDetail = Omit<ApiReceiptDetail, "confirmation_lines"> & {
  confirmation_lines: CorrectionLine[];
};

const deliveryTabs = [
  { href: "/deliveries", label: "Deliveries" },
  { href: "/delivery-exceptions", label: "Exceptions" },
  { href: "/delivery-corrections", label: "Corrections" },
];

function isFailurePayload(
  payload: SignedAccessResponse | Failure,
): payload is Failure {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "kind" in payload &&
    typeof payload.kind === "string" &&
    [
      "conflict",
      "forbidden",
      "unauthenticated",
      "unavailable",
      "validation",
    ].includes(payload.kind)
  );
}

function openReceiptDocument(receiptId: string): void {
  void (async () => {
    try {
      const response = await fetch(`/api/delivery-receipts/${receiptId}`, {
        cache: "no-store",
        method: "POST",
      });
      const payload = (await response.json()) as SignedAccessResponse | Failure;
      if (
        !response.ok ||
        !("access_url" in payload) ||
        typeof payload.access_url !== "string"
      ) {
        window.alert(
          isFailurePayload(payload)
            ? payload.message
            : "The receipt document could not be opened.",
        );
        return;
      }
      window.open(payload.access_url, "_blank", "noopener,noreferrer");
    } catch {
      window.alert("The receipt document could not be reached.");
    }
  })();
}

function ReceiptDocumentLink({
  children,
  receiptId,
}: {
  children: React.ReactNode;
  receiptId: string;
}) {
  return (
    <a
      href="#"
      onClick={(event) => {
        event.preventDefault();
        openReceiptDocument(receiptId);
      }}
      role="link"
      tabIndex={0}
    >
      {children}
    </a>
  );
}

type LoadState =
  { kind: "loading" } | Failure | { items: CorrectionSummary[]; kind: "ready" };
type ActionState = { kind: "idle" | "pending" | "success" } | Failure;

const quantityFields = [
  ["accepted_quantity_base", "Accepted"],
  ["refused_quantity_base", "Refused"],
  ["damaged_quantity_base", "Damaged"],
  ["short_missing_quantity_base", "Short / missing"],
  ["still_undelivered_quantity_base", "Still undelivered"],
] as const;

export function DeliveryCorrectionWorkspace() {
  const [queue, setQueue] = useState<Queue>("pending_authorization");
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [detail, setDetail] = useState<CorrectionDetail | null>(null);
  const [original, setOriginal] = useState<ReceiptDetail | null>(null);
  const [detailState, setDetailState] = useState<ActionState>({ kind: "idle" });
  const [acknowledged, setAcknowledged] = useState(false);
  const [receiptId, setReceiptId] = useState("");
  const [reason, setReason] = useState("");
  const [evidenceIds, setEvidenceIds] = useState("");
  const [proposed, setProposed] = useState<CorrectionLine[]>([]);
  const createIdentity = useRef<{ correctionId: string; key: string } | null>(
    null,
  );
  const authorizationKeys = useRef(new Map<string, string>());
  const directCorrectionOpened = useRef(false);

  const loadQueue = useCallback(
    async (selectedQueue: Exclude<Queue, "request">) => {
      setState({ kind: "loading" });
      try {
        const response = await fetch(
          `/api/delivery-corrections?status=${selectedQueue}`,
          { cache: "no-store" },
        );
        const payload = (await response.json()) as
          Failure | { items: CorrectionSummary[]; total: number };
        setState(
          response.ok
            ? {
                items: (payload as { items: CorrectionSummary[] }).items,
                kind: "ready",
              }
            : (payload as Failure),
        );
      } catch {
        setState(
          serviceFailure("Delivery Correction queue could not be reached."),
        );
      }
    },
    [],
  );

  useEffect(() => {
    if (queue === "request") return;
    const timer = window.setTimeout(() => void loadQueue(queue), 0);
    return () => window.clearTimeout(timer);
  }, [loadQueue, queue]);

  const selectQueue = (value: Queue) => {
    setDetail(null);
    setOriginal(null);
    if (value === "request") {
      setState({ items: [], kind: "ready" });
    }
    setQueue(value);
  };

  const openCorrection = useCallback(async (correctionId: string) => {
    setDetailState({ kind: "pending" });
    setAcknowledged(false);
    try {
      const correctionResponse = await fetch(
        `/api/delivery-corrections/${correctionId}`,
        { cache: "no-store" },
      );
      const correctionPayload = (await correctionResponse.json()) as
        CorrectionDetail | Failure;
      if (!correctionResponse.ok) {
        setDetailState(correctionPayload as Failure);
        return;
      }
      const nextDetail = correctionPayload as CorrectionDetail;
      const receiptResponse = await fetch(
        `/api/delivery-receipts/${nextDetail.original_delivery_receipt_id}`,
        { cache: "no-store" },
      );
      const receiptPayload = (await receiptResponse.json()) as
        ReceiptDetail | Failure;
      if (!receiptResponse.ok) {
        setDetailState(receiptPayload as Failure);
        return;
      }
      const nextOriginal = receiptPayload as ReceiptDetail;
      setQueue(nextDetail.status);
      setDetail(enrichDetail(nextDetail, nextOriginal));
      setOriginal(nextOriginal);
      setDetailState({ kind: "idle" });
    } catch {
      setDetailState(
        serviceFailure("The correction dossier could not be reached."),
      );
    }
  }, []);

  useEffect(() => {
    if (directCorrectionOpened.current) return;
    const correctionId = new URLSearchParams(window.location.search).get(
      "correction",
    );
    if (correctionId === null || correctionId.trim() === "") return;
    directCorrectionOpened.current = true;
    const timer = window.setTimeout(() => void openCorrection(correctionId), 0);
    return () => window.clearTimeout(timer);
  }, [openCorrection]);

  const choose = (item: CorrectionSummary) =>
    openCorrection(item.correction_id);

  const loadOriginal = async () => {
    if (receiptId.trim() === "") return;
    setDetailState({ kind: "pending" });
    try {
      const response = await fetch(
        `/api/delivery-receipts/${receiptId.trim()}`,
        {
          cache: "no-store",
        },
      );
      const payload = (await response.json()) as ReceiptDetail | Failure;
      if (!response.ok) {
        setDetailState(payload as Failure);
        return;
      }
      const receipt = payload as ReceiptDetail;
      setOriginal(receipt);
      setProposed(structuredClone(receipt.confirmation_lines));
      setEvidenceIds("");
      setDetailState({ kind: "idle" });
      createIdentity.current = null;
    } catch {
      setDetailState(
        serviceFailure("The original receipt could not be reached."),
      );
    }
  };

  const editProposal = (
    lineIndex: number,
    field: (typeof quantityFields)[number][0],
    value: string,
    identityIndex?: number,
  ) => {
    createIdentity.current = null;
    setProposed((current) =>
      current.map((line, index) => {
        if (index !== lineIndex) return line;
        if (identityIndex === undefined) return { ...line, [field]: value };
        const identityPositions = line.identity_positions.map(
          (position, positionIndex) =>
            positionIndex === identityIndex
              ? { ...position, [field]: value }
              : position,
        );
        return {
          ...line,
          ...Object.fromEntries(
            quantityFields.map(([quantityField]) => [
              quantityField,
              formatScaled(
                identityPositions.reduce(
                  (sum, position) =>
                    sum + (parseScaled(position[quantityField]) ?? 0n),
                  0n,
                ),
              ),
            ]),
          ),
          identity_positions: identityPositions,
        };
      }),
    );
  };

  const create = async () => {
    if (
      original === null ||
      !isCurrentChainHead(original) ||
      !validProposal(original.confirmation_lines, proposed)
    )
      return;
    let identity = createIdentity.current;
    if (identity === null) {
      const correctionId = crypto.randomUUID();
      identity = {
        correctionId,
        key: `delivery-correction:${correctionId}`,
      };
      createIdentity.current = identity;
    }
    setDetailState({ kind: "pending" });
    try {
      const response = await fetch(
        `/api/delivery-receipts/${original.delivery_receipt_id}/corrections`,
        {
          body: JSON.stringify({
            command: {
              correction_id: identity.correctionId,
              evidence_ids: splitEvidence(evidenceIds),
              lines: proposed.map(commandLine),
              reason: reason.trim(),
            },
            idempotencyKey: identity.key,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as CorrectionDetail | Failure;
      if (!response.ok) {
        setDetailState(payload as Failure);
        return;
      }
      setDetail(enrichDetail(payload as CorrectionDetail, original));
      setDetailState({ kind: "success" });
    } catch {
      setDetailState(
        serviceFailure(
          "The request outcome is uncertain. Retry unchanged work.",
        ),
      );
    }
  };

  const authorize = async () => {
    if (
      detail === null ||
      original === null ||
      detail.status !== "pending_authorization"
    )
      return;
    let key = authorizationKeys.current.get(detail.correction_id);
    if (key === undefined) {
      key = `delivery-correction-authorization:${crypto.randomUUID()}`;
      authorizationKeys.current.set(detail.correction_id, key);
    }
    setDetailState({ kind: "pending" });
    try {
      const response = await fetch(
        `/api/delivery-corrections/${detail.correction_id}/authorization`,
        {
          body: JSON.stringify({
            command: { expected_correction_version: detail.version },
            idempotencyKey: key,
          }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        },
      );
      const payload = (await response.json()) as CorrectionDetail | Failure;
      if (!response.ok) {
        setDetailState(payload as Failure);
        return;
      }
      setDetail(enrichDetail(payload as CorrectionDetail, original));
      setDetailState({ kind: "success" });
    } catch {
      setDetailState(
        serviceFailure(
          "The authorization outcome is uncertain. Retry unchanged work.",
        ),
      );
    }
  };

  const pathname = usePathname();

  const tabs = (
    <>
      {deliveryTabs.map((tab) => (
        <Link
          key={tab.href}
          href={tab.href}
          aria-current={pathname === tab.href ? "page" : undefined}
        >
          {tab.label}
        </Link>
      ))}
    </>
  );

  return (
    <>
      <PageHeader
        description="Compare the issued receipt with one complete proposal. Authorization adds linked reversals and replacements; it never edits the original."
        eyebrow="Delivery"
        tabs={tabs}
        title="Corrections"
      />

      <section className="correction-intro card">
        <p className="eyebrow">Correction register / 008</p>
        <h1>Correct the record. Keep the evidence.</h1>
        <p>
          Compare the issued receipt with one complete proposal. Authorization
          adds linked reversals and replacements; it never edits the original.
        </p>
      </section>
      <nav aria-label="Delivery Correction queues" className="correction-tabs">
        {(
          [
            ["pending_authorization", "Pending approval"],
            ["posted", "Posted chain"],
            ["request", "Request correction"],
          ] as const
        ).map(([value, label]) => (
          <button
            aria-pressed={queue === value}
            key={value}
            onClick={() => selectQueue(value)}
          >
            {label}
          </button>
        ))}
      </nav>

      {queue === "request" ? (
        <RequestWorkspace
          action={detailState}
          detail={detail}
          evidenceIds={evidenceIds}
          onCreate={() => void create()}
          onEditEvidence={(value) => {
            createIdentity.current = null;
            setEvidenceIds(value);
          }}
          onEditProposal={editProposal}
          onEditReason={(value) => {
            createIdentity.current = null;
            setReason(value);
          }}
          onLoad={() => void loadOriginal()}
          original={original}
          proposed={proposed}
          reason={reason}
          receiptId={receiptId}
          setReceiptId={setReceiptId}
        />
      ) : (
        <ReviewWorkspace
          acknowledged={acknowledged}
          action={detailState}
          detail={detail}
          loadState={state}
          onAcknowledge={setAcknowledged}
          onAuthorize={() => void authorize()}
          onChoose={(item) => void choose(item)}
          onRetry={() => void loadQueue(queue)}
          original={original}
        />
      )}
    </>
  );
}

function ReviewWorkspace({
  acknowledged,
  action,
  detail,
  loadState,
  onAcknowledge,
  onAuthorize,
  onChoose,
  onRetry,
  original,
}: {
  acknowledged: boolean;
  action: ActionState;
  detail: CorrectionDetail | null;
  loadState: LoadState;
  onAcknowledge: (value: boolean) => void;
  onAuthorize: () => void;
  onChoose: (item: CorrectionSummary) => void;
  onRetry: () => void;
  original: ReceiptDetail | null;
}) {
  if (loadState.kind === "loading")
    return <PanelState title="Reading correction register" />;
  if (isFailure(loadState))
    return (
      <PanelState
        {...(loadState.kind === "unauthenticated"
          ? {}
          : { action: "Retry register" })}
        detail={`${loadState.message} · ${loadState.correlationId}`}
        onAction={onRetry}
        title={failureTitle(loadState, "Correction register")}
      />
    );
  if (loadState.items.length === 0)
    return (
      <PanelState
        title="No corrections in this queue"
        detail="The scoped correction register is clear."
      />
    );
  return (
    <div className="correction-workspace">
      <section
        aria-label="Delivery Correction register"
        className="correction-ledger"
      >
        <div className="correction-ledger-head">
          <span>Requested</span>
          <span>Receipt / reason</span>
          <span>Value effect</span>
        </div>
        {loadState.items.map((item) => (
          <button key={item.correction_id} onClick={() => onChoose(item)}>
            <time>{formatDate(item.requested_at)}</time>
            <span>
              <b>{item.correction_id}</b>
              <small>{item.reason}</small>
              <small>Maker · {item.requested_by}</small>
            </span>
            <em>
              {item.base_currency} {item.affected_value_base_currency}
            </em>
          </button>
        ))}
      </section>
      <section className="correction-dossier">
        {action.kind === "pending" && detail === null && (
          <PanelState title="Opening immutable dossier" />
        )}
        {detail === null && action.kind !== "pending" && !isFailure(action) && (
          <PanelState
            title="Select a correction"
            detail="Compare every line before authorization."
          />
        )}
        {detail !== null && original !== null && (
          <Dossier detail={detail} original={original}>
            {detail.status === "pending_authorization" && (
              <div className="correction-authorization">
                <p>
                  Requested by <strong>{detail.requested_by}</strong>. The
                  approver must be a different authorized user; proposed
                  quantities are read-only.
                </p>
                <label>
                  <input
                    checked={acknowledged}
                    onChange={(event) => onAcknowledge(event.target.checked)}
                    type="checkbox"
                  />
                  I reviewed the original, evidence, partitions, and posting
                  effects.
                </label>
                <button
                  disabled={
                    !acknowledged ||
                    action.kind === "pending" ||
                    action.kind === "unauthenticated"
                  }
                  onClick={onAuthorize}
                >
                  {action.kind === "pending"
                    ? "Posting immutable chain…"
                    : "Authorize and post correction"}
                </button>
              </div>
            )}
          </Dossier>
        )}
        {isFailure(action) && (
          <ActionFailure
            action={action}
            conflictCopy="Proposal retained. Reload the dossier before authorizing against a new version."
            onReload={() => {
              if (detail !== null) onChoose(detail);
            }}
          />
        )}
      </section>
    </div>
  );
}

function RequestWorkspace({
  action,
  detail,
  evidenceIds,
  onCreate,
  onEditEvidence,
  onEditProposal,
  onEditReason,
  onLoad,
  original,
  proposed,
  reason,
  receiptId,
  setReceiptId,
}: {
  action: ActionState;
  detail: CorrectionDetail | null;
  evidenceIds: string;
  onCreate: () => void;
  onEditEvidence: (value: string) => void;
  onEditProposal: (
    lineIndex: number,
    field: (typeof quantityFields)[number][0],
    value: string,
    identityIndex?: number,
  ) => void;
  onEditReason: (value: string) => void;
  onLoad: () => void;
  original: ReceiptDetail | null;
  proposed: CorrectionLine[];
  reason: string;
  receiptId: string;
  setReceiptId: (value: string) => void;
}) {
  const valid =
    original !== null &&
    reason.trim() !== "" &&
    splitEvidence(evidenceIds).length > 0 &&
    validProposal(original.confirmation_lines, proposed);
  if (detail !== null && action.kind === "success")
    return (
      <section className="correction-requested" aria-live="polite">
        <p className="eyebrow">REQUEST RECORDED</p>
        <h2>Waiting for an independent approver.</h2>
        <p>
          Correction {detail.correction_id} preserves receipt{" "}
          {detail.receipt_effect.original_number}.
        </p>
      </section>
    );
  return (
    <section className="correction-request">
      <div className="correction-source-picker">
        <div>
          <p className="eyebrow">ORIGINAL SOURCE</p>
          <h2>Open the issued receipt.</h2>
        </div>
        <label>
          Delivery Receipt ID
          <input
            value={receiptId}
            onChange={(event) => setReceiptId(event.target.value)}
          />
        </label>
        <button
          disabled={receiptId.trim() === "" || action.kind === "pending"}
          onClick={onLoad}
        >
          Review original
        </button>
      </div>
      {original !== null && (
        <div className="correction-proposal">
          <ChainHeader
            correctionId={null}
            original={original}
            replacementReceiptId={null}
            replacementNumber={null}
            status="pending_authorization"
          />
          {!isCurrentChainHead(original) && (
            <div className="correction-chain-block" role="status">
              <strong>Not the current chain head</strong>
              <span>
                This issued receipt is preserved, but a newer correction owns
                the current operational record.
              </span>
              {original.superseded_by_correction_id !== null && (
                <a
                  href={`/delivery-corrections?correction=${original.superseded_by_correction_id}`}
                >
                  Open correction {original.superseded_by_correction_id}
                </a>
              )}
              {original.replacement_delivery_receipt_id !== null && (
                <ReceiptDocumentLink
                  receiptId={original.replacement_delivery_receipt_id}
                >
                  Open replacement receipt{" "}
                  {original.replacement_delivery_receipt_id}
                </ReceiptDocumentLink>
              )}
            </div>
          )}
          <p className="correction-warning">
            Receipt {original.number} remains issued and readable. Enter one
            complete replacement partition for every line.
          </p>
          {proposed.map((line, lineIndex) => (
            <EditableLine
              key={line.delivery_line_id}
              line={line}
              lineIndex={lineIndex}
              onEdit={onEditProposal}
              original={original.confirmation_lines[lineIndex]!}
            />
          ))}
          <label>
            Correction reason
            <textarea
              aria-label="Correction reason"
              value={reason}
              onChange={(event) => onEditReason(event.target.value)}
            />
          </label>
          <fieldset className="correction-evidence">
            <legend>Source evidence retained with the original receipt</legend>
            {original.evidence_ids.map((evidenceId, index) => (
              <label key={evidenceId}>
                <input
                  checked={splitEvidence(evidenceIds).includes(evidenceId)}
                  onChange={(event) => {
                    const selected = splitEvidence(evidenceIds);
                    onEditEvidence(
                      (event.target.checked
                        ? [...selected, evidenceId]
                        : selected.filter((value) => value !== evidenceId)
                      ).join(","),
                    );
                  }}
                  type="checkbox"
                />
                <span>
                  <strong>Retained proof {index + 1}</strong>
                  <small>{evidenceId}</small>
                </span>
              </label>
            ))}
            {original.evidence_ids.length === 0 && (
              <p>No retained source evidence is available for correction.</p>
            )}
          </fieldset>
          <button
            disabled={!valid || action.kind === "pending"}
            onClick={onCreate}
          >
            {action.kind === "pending"
              ? "Recording immutable proposal…"
              : "Request independent approval"}
          </button>
          {!valid && (
            <small>
              A reason, source evidence, and exact non-negative five-way
              partition are required for every original line and tracked
              identity.
            </small>
          )}
        </div>
      )}
      {isFailure(action) && (
        <ActionFailure
          action={action}
          conflictCopy="Your reason, evidence, and quantities remain. Reload before creating a new proposal identity."
          onReload={onLoad}
        />
      )}
    </section>
  );
}

function Dossier({
  children,
  detail,
  original,
}: {
  children?: React.ReactNode;
  detail: CorrectionDetail;
  original: ReceiptDetail;
}) {
  return (
    <>
      <ChainHeader
        correctionId={detail.correction_id}
        original={original}
        replacementReceiptId={
          detail.receipt_effect.replacement_delivery_receipt_id
        }
        replacementNumber={detail.receipt_effect.replacement_number}
        status={detail.status}
      />
      <div className="correction-meta">
        <p>
          <span>Reason</span>
          <strong>{detail.reason}</strong>
        </p>
        <p>
          <span>Evidence</span>
          <strong>{detail.evidence_ids.length} retained</strong>
        </p>
        <p>
          <span>Maker</span>
          <strong>{detail.requested_by}</strong>
        </p>
        <p>
          <span>Requested</span>
          <strong>{formatDate(detail.requested_at)}</strong>
        </p>
      </div>
      <OriginalSnapshot original={original} />
      <section
        className="correction-comparison"
        aria-label="Original and proposed quantities"
      >
        {detail.lines.map((line) => {
          const source = original.confirmation_lines.find(
            (item) => item.delivery_line_id === line.delivery_line_id,
          );
          return source === undefined ? null : (
            <CompareLine
              key={line.delivery_line_id}
              original={source}
              proposed={line}
            />
          );
        })}
      </section>
      <Effects detail={detail} original={original} />
      <AuditChain detail={detail} />
      {children}
    </>
  );
}

function ChainHeader({
  correctionId,
  original,
  replacementReceiptId,
  replacementNumber,
  status,
}: {
  correctionId: string | null;
  original: ReceiptDetail;
  replacementReceiptId: string | null;
  replacementNumber: string | null;
  status: Status;
}) {
  const previousCorrectionId =
    original.created_by_correction_id === correctionId
      ? null
      : original.created_by_correction_id;
  const noReplacement = status === "posted" && replacementReceiptId === null;
  return (
    <>
      {(previousCorrectionId !== null ||
        original.corrects_delivery_receipt_id !== null) && (
        <nav
          aria-label="Receipt correction lineage"
          className="correction-lineage"
        >
          <span>Earlier in this immutable chain</span>
          {original.corrects_delivery_receipt_id !== null && (
            <ReceiptDocumentLink
              receiptId={original.corrects_delivery_receipt_id}
            >
              Previous receipt {original.corrects_delivery_receipt_id}
            </ReceiptDocumentLink>
          )}
          {previousCorrectionId !== null && (
            <a
              href={`/delivery-corrections?correction=${previousCorrectionId}`}
            >
              Previous correction {previousCorrectionId}
            </a>
          )}
        </nav>
      )}
      <div className="correction-chain" aria-label="Receipt correction chain">
        <div className="current">
          <small>
            Source · {original.correction_status.replaceAll("_", " ")}
          </small>
          <ReceiptDocumentLink receiptId={original.delivery_receipt_id}>
            {original.number}
          </ReceiptDocumentLink>
          <span>{original.status.replaceAll("_", " ")} · preserved</span>
        </div>
        <i aria-hidden="true">→</i>
        <div>
          <small>Correction</small>
          <strong>{status === "posted" ? "Posted" : "Approval pending"}</strong>
          <span>{correctionId ?? "new proposal"}</span>
        </div>
        <i aria-hidden="true">→</i>
        <div className={replacementNumber === null ? "muted" : "replacement"}>
          <small>Replacement</small>
          {replacementReceiptId === null ? (
            <strong>
              {noReplacement
                ? "No replacement receipt — accepted total is zero"
                : "Assigned only if posted"}
            </strong>
          ) : (
            <ReceiptDocumentLink receiptId={replacementReceiptId}>
              {replacementNumber}
            </ReceiptDocumentLink>
          )}
          <span>
            {noReplacement
              ? "Branch series remains unconsumed"
              : "new Branch-series identity"}
          </span>
        </div>
      </div>
    </>
  );
}

function OriginalSnapshot({ original }: { original: ReceiptDetail }) {
  const snapshot = original.snapshot;
  return (
    <section
      className="correction-snapshot"
      aria-label="Original receipt snapshot"
    >
      <p className="eyebrow">ORIGINAL SNAPSHOT</p>
      <div>
        <p>
          <span>Customer</span>
          <strong>{snapshotText(snapshot.customer_legal_name)}</strong>
          <small>{snapshotText(snapshot.customer_account_number)}</small>
        </p>
        <p>
          <span>Recipient</span>
          <strong>{snapshotText(snapshot.recipient_name)}</strong>
          <small>{snapshotText(snapshot.delivery_address)}</small>
        </p>
        <p>
          <span>Proof of Delivery</span>
          <strong>
            {original.evidence_ids.length} retained evidence items
          </strong>
          <small>{original.evidence_ids.join(" · ")}</small>
        </p>
      </div>
    </section>
  );
}

function CompareLine({
  original,
  proposed,
}: {
  original: CorrectionLine;
  proposed: CorrectionLine;
}) {
  return (
    <article>
      <header>
        <div>
          <small>SKU</small>
          <strong>{proposed.sku_id}</strong>
        </div>
        <code>{proposed.delivery_line_id}</code>
      </header>
      <div className="quantity-table">
        <span>Outcome</span>
        <span>Issued</span>
        <span>Proposed</span>
        <span>Delta</span>
        {quantityFields.map(([field, label]) => (
          <Quantities
            key={field}
            field={field}
            label={label}
            original={original}
            proposed={proposed}
          />
        ))}
      </div>
      {proposed.identity_positions !== undefined &&
        proposed.identity_positions.length > 0 && (
          <details>
            <summary>
              {proposed.identity_positions.length} tracked identity partitions
            </summary>
            {proposed.identity_positions.map((position, index) => (
              <div
                className="identity-row"
                key={position.delivery_line_identity_allocation_id}
              >
                <strong>
                  {position.serial_number ??
                    position.lot_code ??
                    `Identity ${index + 1}`}
                </strong>
                <span>{position.quantity_base}</span>
                <small>
                  {quantityFields
                    .map(([field, label]) => `${label}: ${position[field]}`)
                    .join(" · ")}
                </small>
              </div>
            ))}
          </details>
        )}
    </article>
  );
}

function Quantities({
  field,
  label,
  original,
  proposed,
}: {
  field: (typeof quantityFields)[number][0];
  label: string;
  original: CorrectionLine;
  proposed: CorrectionLine;
}) {
  const delta =
    (parseScaled(proposed[field]) ?? 0n) - (parseScaled(original[field]) ?? 0n);
  return (
    <>
      <b>{label}</b>
      <span>{original[field]}</span>
      <span>{proposed[field]}</span>
      <em className={delta < 0n ? "negative" : delta > 0n ? "positive" : ""}>
        {delta === 0n ? "—" : `${delta > 0n ? "+" : ""}${formatScaled(delta)}`}
      </em>
    </>
  );
}

function EditableLine({
  line,
  lineIndex,
  onEdit,
  original,
}: {
  line: CorrectionLine;
  lineIndex: number;
  onEdit: (
    lineIndex: number,
    field: (typeof quantityFields)[number][0],
    value: string,
    identityIndex?: number,
  ) => void;
  original: CorrectionLine;
}) {
  return (
    <article className="correction-edit-line">
      <header>
        <div>
          <small>Delivery line</small>
          <strong>{line.delivery_line_id}</strong>
        </div>
        <span>Original total · {formatScaled(lineTotal(original))}</span>
      </header>
      <div className="correction-input-grid">
        {quantityFields.map(([field, label]) => (
          <label key={field}>
            {label}
            <input
              aria-label={`${label} quantity for ${line.delivery_line_id}`}
              inputMode="decimal"
              readOnly={
                line.identity_positions !== undefined &&
                line.identity_positions.length > 0
              }
              value={line[field]}
              onChange={(event) => onEdit(lineIndex, field, event.target.value)}
            />
          </label>
        ))}
      </div>
      {line.identity_positions !== undefined &&
        line.identity_positions.map((position, identityIndex) => (
          <fieldset key={position.delivery_line_identity_allocation_id}>
            <legend>
              {position.serial_number ?? position.lot_code} · tracked quantity{" "}
              {position.quantity_base}
            </legend>
            <div className="correction-input-grid">
              {quantityFields.map(([field, label]) => (
                <label key={field}>
                  {label}
                  <input
                    aria-label={`${label} quantity for ${position.serial_number ?? position.lot_code}`}
                    inputMode="decimal"
                    value={position[field]}
                    onChange={(event) =>
                      onEdit(
                        lineIndex,
                        field,
                        event.target.value,
                        identityIndex,
                      )
                    }
                  />
                </label>
              ))}
            </div>
          </fieldset>
        ))}
    </article>
  );
}

function Effects({
  detail,
}: {
  detail: CorrectionDetail;
  original: ReceiptDetail;
}) {
  const pending = detail.status === "pending_authorization";
  const expectedReversals = detail.stock_effect.expected_reversal_count;
  const expectedReplacements = detail.stock_effect.expected_replacement_count;
  const replacementInvoiceExpected = pending
    ? detail.lines.some((line) => hasPositive(line.accepted_quantity_base))
    : detail.draft_invoice_effect.replacement_draft_invoice_id !== null;
  const replacementReceiptExpected = pending
    ? replacementInvoiceExpected
    : detail.receipt_effect.replacement_delivery_receipt_id !== null;
  return (
    <section className="correction-effects">
      <p className="eyebrow">POSTING EFFECTS</p>
      <div>
        <article>
          <small>Stock / original MAC</small>
          <strong>
            {pending
              ? "Expected"
              : detail.stock_effect.status.replaceAll("_", " ")}
          </strong>
          <span>
            {expectedReversals} reversal{expectedReversals === 1 ? "" : "s"} ·{" "}
            {expectedReplacements} replacement
            {expectedReplacements === 1 ? "" : "s"}
          </span>
        </article>
        <article>
          <small>Draft Invoice source</small>
          <strong>
            {pending
              ? replacementInvoiceExpected
                ? "Reverse and replace"
                : "Reverse only"
              : detail.draft_invoice_effect.replacement_draft_invoice_id !==
                  null
                ? "Reversed and replaced"
                : "Reversed only"}
          </strong>
          <span>
            {replacementInvoiceExpected
              ? "Reverse original; replace with corrected accepted total"
              : "No replacement source — corrected accepted total is zero"}
          </span>
        </article>
        <article>
          <small>Receipt identity</small>
          <strong>
            {detail.receipt_effect.replacement_number ??
              (pending
                ? replacementReceiptExpected
                  ? "New Branch-series number"
                  : "No replacement receipt"
                : detail.status === "posted"
                  ? "No replacement receipt"
                  : "Pending authorization")}
          </strong>
          <span>The original number is never reused</span>
        </article>
      </div>
    </section>
  );
}

function hasPositive(value: string): boolean {
  return (parseScaled(value) ?? 0n) > 0n;
}

function AuditChain({ detail }: { detail: CorrectionDetail }) {
  return (
    <ol className="correction-audit" aria-label="Complete audit chain">
      <li>
        <small>Original issued</small>
        <strong>{detail.receipt_effect.original_number}</strong>
        <span>{detail.receipt_effect.original_delivery_receipt_id}</span>
      </li>
      <li>
        <small>Requested · {formatDate(detail.requested_at)}</small>
        <strong>{detail.requested_by}</strong>
        <span>{detail.correction_id}</span>
      </li>
      {detail.authorized_at !== null && (
        <li>
          <small>Authorized · {formatDate(detail.authorized_at)}</small>
          <strong>{detail.authorized_by}</strong>
          <span>{detail.outbox_event_id ?? "Outbox identity pending"}</span>
        </li>
      )}
      {detail.receipt_effect.replacement_delivery_receipt_id !== null && (
        <li>
          <small>Replacement issued</small>
          <strong>{detail.receipt_effect.replacement_number}</strong>
          <span>{detail.receipt_effect.replacement_delivery_receipt_id}</span>
        </li>
      )}
    </ol>
  );
}

function ActionFailure({
  action,
  conflictCopy,
  onReload,
}: {
  action: Failure;
  conflictCopy: string;
  onReload: () => void;
}) {
  return (
    <div className={`correction-action-state ${action.kind}`} role="status">
      <strong>{failureTitle(action, "Correction not posted")}</strong>
      <p>
        {action.message} · {action.correlationId}
      </p>
      {action.kind === "conflict" && <small>{conflictCopy}</small>}
      {action.kind === "conflict" && (
        <button onClick={onReload}>Reload current record</button>
      )}
    </div>
  );
}

function PanelState({
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
    <section aria-live="polite" className="correction-state">
      <h2>{title}</h2>
      {detail !== undefined && <p>{detail}</p>}
      {action !== undefined && onAction !== undefined && (
        <button onClick={onAction}>{action}</button>
      )}
    </section>
  );
}

function commandLine(line: CorrectionLine) {
  return {
    accepted_quantity_base: line.accepted_quantity_base,
    damaged_quantity_base: line.damaged_quantity_base,
    delivery_line_id: line.delivery_line_id,
    identity_positions: line.identity_positions.map((position) => ({
      accepted_quantity_base: position.accepted_quantity_base,
      damaged_quantity_base: position.damaged_quantity_base,
      delivery_line_identity_allocation_id:
        position.delivery_line_identity_allocation_id,
      refused_quantity_base: position.refused_quantity_base,
      short_missing_quantity_base: position.short_missing_quantity_base,
      still_undelivered_quantity_base: position.still_undelivered_quantity_base,
    })),
    refused_quantity_base: line.refused_quantity_base,
    short_missing_quantity_base: line.short_missing_quantity_base,
    still_undelivered_quantity_base: line.still_undelivered_quantity_base,
  };
}

function enrichDetail(
  detail: ApiCorrectionDetail,
  original: ReceiptDetail,
): CorrectionDetail {
  return {
    ...detail,
    lines: detail.lines.map((proposal) => {
      const source = original.confirmation_lines.find(
        (line) => line.delivery_line_id === proposal.delivery_line_id,
      );
      if (source === undefined) return proposal as CorrectionLine;
      const apiPositions = proposal.identity_positions as
        IdentityPosition[] | undefined;
      return {
        ...source,
        ...proposal,
        identity_positions:
          apiPositions === undefined
            ? source.identity_positions
            : apiPositions.map((position) => {
                const sourcePosition = source.identity_positions.find(
                  (item) =>
                    item.delivery_line_identity_allocation_id ===
                    position.delivery_line_identity_allocation_id,
                );
                return sourcePosition === undefined
                  ? position
                  : { ...sourcePosition, ...position };
              }),
      };
    }),
  };
}

function validProposal(original: CorrectionLine[], proposed: CorrectionLine[]) {
  if (original.length === 0 || original.length !== proposed.length)
    return false;
  return proposed.every((line) => {
    const source = original.find(
      (item) => item.delivery_line_id === line.delivery_line_id,
    );
    if (source === undefined || lineTotal(line) !== lineTotal(source))
      return false;
    if (!quantityFields.every(([field]) => canonicalQuantity(line[field])))
      return false;
    const identityPositions = line.identity_positions ?? [];
    const sourcePositions = source.identity_positions ?? [];
    if (identityPositions.length !== sourcePositions.length) return false;
    if (identityPositions.length === 0) return true;
    if (
      !identityPositions.every(
        (position) =>
          canonicalQuantity(position.quantity_base) &&
          quantityFields.every(([field]) =>
            canonicalQuantity(position[field]),
          ) &&
          positionTotal(position) === parseScaled(position.quantity_base) &&
          (position.tracking_policy !== "serial" || serialOneHot(position)),
      )
    )
      return false;
    return quantityFields.every(
      ([field]) =>
        parseScaled(line[field]) ===
        identityPositions.reduce(
          (sum, position) => sum + (parseScaled(position[field]) ?? 0n),
          0n,
        ),
    );
  });
}

function isCurrentChainHead(receipt: ReceiptDetail) {
  return (
    receipt.correction_status !== "corrected" &&
    receipt.superseded_by_correction_id === null
  );
}

function lineTotal(line: CorrectionLine) {
  return quantityFields.reduce(
    (sum, [field]) => sum + (parseScaled(line[field]) ?? 0n),
    0n,
  );
}
function positionTotal(position: IdentityPosition) {
  return quantityFields.reduce(
    (sum, [field]) => sum + (parseScaled(position[field]) ?? 0n),
    0n,
  );
}
function canonicalQuantity(value: string) {
  return parseScaled(value) !== null;
}
function parseScaled(value: string): bigint | null {
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,6}))?$/.exec(value);
  if (match === null) return null;
  return (
    BigInt(match[1]!) * 1_000_000n + BigInt((match[2] ?? "").padEnd(6, "0"))
  );
}
function formatScaled(value: bigint): string {
  const sign = value < 0n ? "-" : "";
  const absolute = value < 0n ? -value : value;
  return `${sign}${absolute / 1_000_000n}.${(absolute % 1_000_000n)
    .toString()
    .padStart(6, "0")}`;
}
function serialOneHot(position: IdentityPosition): boolean {
  return (
    parseScaled(position.quantity_base) === 1_000_000n &&
    quantityFields.filter(
      ([field]) => parseScaled(position[field]) === 1_000_000n,
    ).length === 1 &&
    quantityFields.filter(([field]) => parseScaled(position[field]) === 0n)
      .length === 4
  );
}
function splitEvidence(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter((item) => isUuid(item));
}
function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value,
  );
}
function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-PH", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
function snapshotText(value: unknown): string {
  if (typeof value === "string" && value.trim() !== "") return value;
  if (value !== null && typeof value === "object")
    return Object.values(value)
      .filter((item): item is string => typeof item === "string" && item !== "")
      .join(", ");
  return "Not captured";
}
function serviceFailure(message: string): Failure {
  return {
    code: "delivery_correction_service_unavailable",
    correlationId: crypto.randomUUID(),
    kind: "unavailable",
    message,
  };
}
function isFailure(value: { kind: string }): value is Failure {
  return [
    "conflict",
    "forbidden",
    "unauthenticated",
    "unavailable",
    "validation",
  ].includes(value.kind);
}
function failureTitle(failure: Failure, fallback: string) {
  if (failure.kind === "unauthenticated") return "Sign in required";
  if (failure.kind === "forbidden") return "Action forbidden";
  if (failure.kind === "conflict") return "Record changed — review required";
  return fallback;
}
