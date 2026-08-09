import type { AssignedDelivery } from "@tradeflow/delivery-dispatch";
import { colors } from "@tradeflow/design-tokens";
import { CryptoDigestAlgorithm, digest, randomUUID } from "expo-crypto";
import * as ImagePicker from "expo-image-picker";
import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import type {
  DeliveryConfirmationCapture as DeliveryConfirmationCaptureValue,
  DeliveryConfirmationStore,
  LocalDeliveryConfirmation,
  LocalDeliveryEvidence,
} from "../offline/delivery-confirmation-store";
import { persistEvidenceFile } from "../offline/durable-evidence-file";
import {
  acceptsDeliveryQuantity,
  createInitialPartitions,
  DeliveryPartitionEditor,
  exceptionOutcomes,
  partitionsAreValid,
} from "./delivery-partition-editor";

export function DeliveryConfirmationCapture({
  delivery,
  now = () => new Date().toISOString(),
  onSaved,
  persistEvidence = persistEvidenceFile,
  quoteCOD,
  replacesConfirmationId,
  replacementSource,
  store,
}: {
  delivery: AssignedDelivery;
  now?: () => string;
  onSaved: () => void;
  persistEvidence?: (
    sourceUri: string,
    evidenceId: string,
    extension: string,
  ) => Promise<string>;
  quoteCOD?: (input: {
    deliveryId: string;
    expectedDeliveryVersion: number;
    lines: ReturnType<typeof createInitialPartitions>;
  }) => Promise<{
    accepted_quantity_base: string;
    amount_due: string;
    currency: string;
    delivery_id: string;
    delivery_version: number;
  }>;
  replacesConfirmationId?: string;
  replacementSource?: LocalDeliveryConfirmation;
  store: DeliveryConfirmationStore;
}) {
  const [recipient, setRecipient] = useState(
    replacementSource?.command.recipient_name ?? delivery.recipientName,
  );
  const [notes, setNotes] = useState(replacementSource?.command.notes ?? "");
  const [partitions, setPartitions] = useState(
    () => replacementSource?.command.lines ?? createInitialPartitions(delivery),
  );
  const [partialQuote, setPartialQuote] = useState<{
    amount: string;
    currency: string;
  } | null>(null);
  const [evidence, setEvidence] = useState<LocalDeliveryEvidence[]>(
    replacementSource?.evidence ?? [],
  );
  const [cashCollected, setCashCollected] = useState(
    replacementSource?.command.collection?.amount?.toString() ??
      delivery.collectionAmountDue ??
      "",
  );
  const [settlementMode, setSettlementMode] = useState<
    "cash" | "noncash" | "on_account"
  >(
    replacementSource?.command.on_account_conversion_id != null
      ? "on_account"
      : replacementSource?.command.collection?.payment_method === "cash" ||
          replacementSource?.command.collection == null
        ? "cash"
        : "noncash",
  );
  const [noncashMethod, setNoncashMethod] = useState<
    "bank_transfer" | "check" | "electronic"
  >("bank_transfer");
  const [paymentReceiptId, setPaymentReceiptId] = useState(
    replacementSource?.command.collection?.payment_receipt_id ?? "",
  );
  const [conversionId, setConversionId] = useState(
    replacementSource?.command.on_account_conversion_id ?? "",
  );
  const [message, setMessage] = useState<string | null>(null);
  const signature = evidence.find((item) => item.kind === "signature");
  const acceptsQuantity = acceptsDeliveryQuantity(partitions);

  const capture = async (kind: "photo" | "signature") => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      setMessage("Camera permission is required to capture Delivery evidence.");
      return;
    }
    const result = await ImagePicker.launchCameraAsync({
      allowsEditing: false,
      mediaTypes: ["images"],
      quality: 0.85,
    });
    if (result.canceled) return;
    const asset = result.assets[0];
    if (asset === undefined) return;
    const contentType = contentTypeFor(asset.mimeType);
    if (contentType === null) {
      setMessage("Evidence must be JPEG, PNG, or WebP.");
      return;
    }
    const bytes = await (await fetch(asset.uri)).arrayBuffer();
    if (bytes.byteLength > 10 * 1024 * 1024) {
      setMessage("Evidence must not exceed 10 MiB.");
      return;
    }
    const checksum = await digest(CryptoDigestAlgorithm.SHA256, bytes);
    const evidenceId = randomUUID();
    const localUri = await persistEvidence(
      asset.uri,
      evidenceId,
      extensionFor(contentType),
    );
    const next: LocalDeliveryEvidence = {
      contentType,
      evidenceId,
      kind,
      localUri,
      sha256: hex(checksum),
      sizeBytes: bytes.byteLength,
      status: "pending_upload",
    };
    setEvidence((current) =>
      kind === "signature"
        ? [...current.filter((item) => item.kind !== "signature"), next]
        : [...current, next],
    );
    setMessage(null);
  };

  const queue = async () => {
    if (recipient.trim().length === 0 || signature === undefined) {
      setMessage("Recipient name and signature evidence are required.");
      return;
    }
    if (!partitionsAreValid(delivery, partitions)) {
      setMessage("Every line outcome must equal its dispatched quantity.");
      return;
    }
    for (const line of partitions) {
      for (const [outcome, field] of exceptionOutcomes) {
        if (quantityIsZero(line[field])) continue;
        const detail = line.exception_details[outcome];
        if (detail === undefined || detail.reason.trim() === "") {
          setMessage(
            `Enter a reason for every ${outcome.replaceAll("_", " ")} outcome.`,
          );
          return;
        }
        if (detail.evidence_ids.length === 0) {
          setMessage(
            `Assign a photo to every ${outcome.replaceAll("_", " ")} outcome.`,
          );
          return;
        }
      }
    }
    if (
      delivery.collectionRequired &&
      acceptsQuantity &&
      delivery.collectionAmountDue === null
    ) {
      setMessage(
        "The server-calculated COD due is unavailable. Refresh the Delivery before capture.",
      );
      return;
    }
    const partialAcceptance = partitions.some(
      (line) =>
        line.accepted_quantity_base !==
        delivery.lines.find(
          (item) => item.deliveryLineId === line.delivery_line_id,
        )?.quantityBase,
    );
    let collectionAmount = cashCollected;
    let collectionCurrency = "PHP";
    if (delivery.collectionRequired && acceptsQuantity && partialAcceptance) {
      if (partialQuote === null) {
        if (quoteCOD === undefined) {
          setMessage(
            "Connect to load the exact accepted-quantity COD due before saving.",
          );
          return;
        }
        try {
          const quote = await quoteCOD({
            deliveryId: delivery.deliveryId,
            expectedDeliveryVersion: delivery.version,
            lines: partitions,
          });
          if (
            quote.delivery_id !== delivery.deliveryId ||
            quote.delivery_version !== delivery.version ||
            !quantitiesEqual(
              quote.accepted_quantity_base,
              partitions.map((line) => line.accepted_quantity_base),
            )
          ) {
            setMessage(
              "Delivery custody changed. Refresh before collecting COD.",
            );
            return;
          }
          setPartialQuote({
            amount: quote.amount_due,
            currency: quote.currency,
          });
          setCashCollected(quote.amount_due);
          setMessage(
            `Exact accepted-quantity due loaded: ${quote.currency} ${quote.amount_due}. Verify collection, then save again.`,
          );
          return;
        } catch {
          setMessage(
            "Exact accepted-quantity COD due is unavailable. Reconnect and retry.",
          );
          return;
        }
      }
      collectionAmount = partialQuote.amount;
      collectionCurrency = partialQuote.currency;
      if (cashCollected !== collectionAmount) {
        setMessage(
          `Collection must exactly match ${collectionCurrency} ${collectionAmount}.`,
        );
        return;
      }
    }
    if (
      delivery.collectionRequired &&
      acceptsQuantity &&
      settlementMode !== "on_account" &&
      !isCanonicalPositiveDecimal(cashCollected)
    ) {
      setMessage(
        "Collection must be a positive decimal. The server will validate the exact accepted-quantity due.",
      );
      return;
    }
    if (
      delivery.collectionRequired &&
      acceptsQuantity &&
      settlementMode === "noncash" &&
      paymentReceiptId.trim().length === 0
    ) {
      setMessage("Enter the cleared non-cash Payment Receipt ID.");
      return;
    }
    if (
      delivery.collectionRequired &&
      acceptsQuantity &&
      settlementMode === "on_account" &&
      conversionId.trim().length === 0
    ) {
      setMessage("Enter the approved On Account conversion ID.");
      return;
    }
    const confirmationId = randomUUID();
    const capturedAt = now();
    const captureValue: DeliveryConfirmationCaptureValue = {
      command: {
        confirmation_id: confirmationId,
        device_captured_at: capturedAt,
        evidence_ids: evidence.map((item) => item.evidenceId),
        expected_delivery_version: delivery.version,
        lines: partitions.map((line) => ({
          ...line,
          exception_details: Object.fromEntries(
            Object.entries(line.exception_details).filter(([outcome]) => {
              const match = exceptionOutcomes.find(
                ([value]) => value === outcome,
              );
              return match !== undefined && !quantityIsZero(line[match[1]]);
            }),
          ),
        })),
        notes: notes.trim() || null,
        recipient_name: recipient.trim(),
        ...(delivery.collectionRequired &&
        acceptsQuantity &&
        settlementMode !== "on_account"
          ? {
              collection: {
                amount: collectionAmount,
                currency: collectionCurrency,
                evidence: null,
                external_reference: null,
                payment_method:
                  settlementMode === "cash" ? "cash" : noncashMethod,
                payment_receipt_id:
                  settlementMode === "cash"
                    ? randomUUID()
                    : paymentReceiptId.trim(),
                received_at: capturedAt,
              },
            }
          : {}),
        ...(delivery.collectionRequired &&
        acceptsQuantity &&
        settlementMode === "on_account"
          ? { on_account_conversion_id: conversionId.trim() }
          : {}),
      },
      deliveryId: delivery.deliveryId,
      evidence,
      idempotencyKey: `delivery-confirmation:${confirmationId}`,
    };
    if (replacesConfirmationId === undefined) {
      await store.saveAndEnqueue(captureValue, now());
    } else {
      await store.replaceConflict(replacesConfirmationId, captureValue, now());
    }
    onSaved();
  };

  return (
    <View style={styles.panel}>
      <Text style={styles.eyebrow}>ACCEPTED DELIVERY</Text>
      <Text accessibilityRole="header" style={styles.title}>
        {delivery.collectionRequired
          ? "Capture COD payment and Proof of Delivery"
          : "Capture Proof of Delivery"}
      </Text>
      {delivery.collectionRequired && (
        <View style={styles.collection}>
          <Text style={styles.help}>
            Full-delivery COD quote: PHP{" "}
            {delivery.collectionAmountDue ?? "Unavailable"}
          </Text>
          <View style={styles.actions}>
            {(["cash", "noncash", "on_account"] as const).map((value) => (
              <Pressable key={value} onPress={() => setSettlementMode(value)}>
                <Text style={styles.link}>
                  {settlementMode === value ? "● " : "○ "}
                  {value.replaceAll("_", " ").toUpperCase()}
                </Text>
              </Pressable>
            ))}
          </View>
          {settlementMode !== "on_account" && (
            <TextInput
              accessibilityLabel="COD amount collected"
              keyboardType="decimal-pad"
              onChangeText={setCashCollected}
              placeholder="0.00"
              style={styles.input}
              value={cashCollected}
            />
          )}
          {settlementMode === "noncash" && (
            <>
              <View style={styles.actions}>
                {(["bank_transfer", "check", "electronic"] as const).map(
                  (value) => (
                    <Pressable
                      key={value}
                      onPress={() => setNoncashMethod(value)}
                    >
                      <Text style={styles.link}>
                        {noncashMethod === value ? "● " : "○ "}
                        {value.replaceAll("_", " ").toUpperCase()}
                      </Text>
                    </Pressable>
                  ),
                )}
              </View>
              <TextInput
                accessibilityLabel="Cleared Payment Receipt ID"
                onChangeText={setPaymentReceiptId}
                placeholder="Payment Receipt UUID"
                style={styles.input}
                value={paymentReceiptId}
              />
            </>
          )}
          {settlementMode === "on_account" && (
            <TextInput
              accessibilityLabel="Approved On Account conversion ID"
              onChangeText={setConversionId}
              placeholder="Conversion UUID"
              style={styles.input}
              value={conversionId}
            />
          )}
          <Text style={styles.help}>
            Partial outcomes are repriced from accepted quantity by the server.
            Settlement and proof stay Pending Sync until acknowledgement.
          </Text>
        </View>
      )}
      <TextInput
        accessibilityLabel="Recipient name"
        onChangeText={setRecipient}
        style={styles.input}
        value={recipient}
      />
      <TextInput
        accessibilityLabel="Delivery notes"
        multiline
        onChangeText={setNotes}
        placeholder="Condition, handoff, or recipient note"
        style={[styles.input, styles.notes]}
        value={notes}
      />
      <DeliveryPartitionEditor
        delivery={delivery}
        onChange={(next) => {
          setPartitions(next);
          setPartialQuote(null);
          if (
            next.some(
              (line) =>
                line.accepted_quantity_base !==
                delivery.lines.find(
                  (item) => item.deliveryLineId === line.delivery_line_id,
                )?.quantityBase,
            )
          )
            setCashCollected("");
        }}
        value={partitions}
      />
      {partitions.map((line) =>
        exceptionOutcomes.map(([outcome, field, label]) => {
          if (quantityIsZero(line[field])) return null;
          const detail = line.exception_details[outcome] ?? {
            evidence_ids: [],
            reason: "",
            responsible_party_type: "unknown" as const,
          };
          const photos = evidence.filter((item) => item.kind === "photo");
          const update = (next: typeof detail) =>
            setPartitions((current) =>
              current.map((candidate) =>
                candidate.delivery_line_id === line.delivery_line_id
                  ? {
                      ...candidate,
                      exception_details: {
                        ...candidate.exception_details,
                        [outcome]: next,
                      },
                    }
                  : candidate,
              ),
            );
          return (
            <View
              key={`${line.delivery_line_id}:${outcome}`}
              style={styles.exceptionDetail}
            >
              <Text style={styles.eyebrow}>{label.toUpperCase()} CONTROL</Text>
              <TextInput
                accessibilityLabel={`${label} reason`}
                onChangeText={(reason) => update({ ...detail, reason })}
                placeholder="Controlled outcome reason"
                style={styles.input}
                value={detail.reason}
              />
              <View style={styles.actions}>
                {(["carrier", "customer", "staff", "unknown"] as const).map(
                  (party) => (
                    <Pressable
                      key={party}
                      onPress={() =>
                        update({ ...detail, responsible_party_type: party })
                      }
                    >
                      <Text style={styles.link}>
                        {detail.responsible_party_type === party ? "● " : "○ "}
                        {party.toUpperCase()}
                      </Text>
                    </Pressable>
                  ),
                )}
              </View>
              <Pressable
                accessibilityRole="button"
                onPress={() =>
                  update({
                    ...detail,
                    evidence_ids: photos.map((item) => item.evidenceId),
                  })
                }
              >
                <Text style={styles.link}>
                  ASSIGN {photos.length} PHOTO{photos.length === 1 ? "" : "S"}
                </Text>
              </Pressable>
            </View>
          );
        }),
      )}
      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          onPress={() => void capture("signature")}
        >
          <Text style={styles.link}>
            {signature === undefined ? "CAPTURE SIGNATURE" : "RETAKE SIGNATURE"}
          </Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          onPress={() => void capture("photo")}
        >
          <Text style={styles.link}>ADD PHOTO</Text>
        </Pressable>
      </View>
      <Text style={styles.help}>
        {signature === undefined ? "Signature required" : "Signature captured"}{" "}
        · {evidence.filter((item) => item.kind === "photo").length} photos
      </Text>
      {message !== null && <Text style={styles.error}>{message}</Text>}
      <Pressable
        accessibilityRole="button"
        onPress={() => void queue()}
        style={styles.submit}
      >
        <Text style={styles.submitText}>
          {delivery.collectionRequired
            ? "SAVE COD TO PENDING SYNC"
            : "SAVE TO PENDING SYNC"}
        </Text>
      </Pressable>
    </View>
  );
}

function isCanonicalPositiveDecimal(value: string): boolean {
  return /^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(value) && Number(value) > 0;
}

function quantityIsZero(value: string): boolean {
  return /^(?:0+)(?:\.0{1,6})?$/.test(value);
}

function quantitiesEqual(total: string, parts: string[]): boolean {
  const parse = (value: string) => {
    const match = /^(0|[1-9]\d*)(?:\.(\d{1,6}))?$/.exec(value);
    if (match === null) return null;
    return (
      BigInt(match[1] ?? "0") * 1_000_000n +
      BigInt((match[2] ?? "").padEnd(6, "0"))
    );
  };
  const target = parse(total);
  const values = parts.map(parse);
  return (
    target !== null &&
    values.every((value) => value !== null) &&
    values.reduce<bigint>((sum, value) => sum + (value ?? 0n), 0n) === target
  );
}

function contentTypeFor(
  value: string | undefined,
): LocalDeliveryEvidence["contentType"] | null {
  if (value === "image/jpeg" || value === "image/png" || value === "image/webp")
    return value;
  return null;
}

function extensionFor(value: LocalDeliveryEvidence["contentType"]): string {
  if (value === "image/jpeg") return "jpg";
  if (value === "image/webp") return "webp";
  return "png";
}

function hex(value: ArrayBuffer): string {
  return [...new Uint8Array(value)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", gap: 24, marginVertical: 14 },
  collection: {
    borderLeftColor: colors.orange,
    borderLeftWidth: 3,
    paddingLeft: 12,
  },
  error: { color: colors.red, marginTop: 10 },
  exceptionDetail: {
    borderLeftColor: colors.orange,
    borderLeftWidth: 2,
    marginTop: 16,
    paddingLeft: 12,
  },
  eyebrow: {
    color: colors.orange,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.4,
  },
  help: { color: colors.inkMuted, lineHeight: 20 },
  input: {
    borderBottomColor: colors.ink,
    borderBottomWidth: 1,
    color: colors.ink,
    marginTop: 14,
    paddingVertical: 10,
  },
  link: { color: colors.orange, fontSize: 12, fontWeight: "700" },
  notes: { minHeight: 72, textAlignVertical: "top" },
  panel: { backgroundColor: colors.paper, padding: 20 },
  submit: { backgroundColor: colors.ink, marginTop: 18, padding: 15 },
  submitText: {
    color: colors.paper,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1,
  },
  title: {
    color: colors.ink,
    fontSize: 24,
    fontWeight: "700",
    marginBottom: 8,
    marginTop: 6,
  },
});
