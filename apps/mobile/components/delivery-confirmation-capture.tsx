import type { AssignedDelivery } from "@tradeflow/delivery-dispatch";
import { colors } from "@tradeflow/design-tokens";
import { CryptoDigestAlgorithm, digest, randomUUID } from "expo-crypto";
import * as ImagePicker from "expo-image-picker";
import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import type {
  DeliveryConfirmationStore,
  LocalDeliveryEvidence,
} from "../offline/delivery-confirmation-store";
import { persistEvidenceFile } from "../offline/durable-evidence-file";

export function DeliveryConfirmationCapture({
  delivery,
  now = () => new Date().toISOString(),
  onSaved,
  persistEvidence = persistEvidenceFile,
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
  store: DeliveryConfirmationStore;
}) {
  const [recipient, setRecipient] = useState(delivery.recipientName);
  const [notes, setNotes] = useState("");
  const [evidence, setEvidence] = useState<LocalDeliveryEvidence[]>([]);
  const [cashCollected, setCashCollected] = useState(
    delivery.collectionAmountDue ?? "",
  );
  const [settlementMode, setSettlementMode] = useState<
    "cash" | "noncash" | "on_account"
  >("cash");
  const [noncashMethod, setNoncashMethod] = useState<
    "bank_transfer" | "check" | "electronic"
  >("bank_transfer");
  const [paymentReceiptId, setPaymentReceiptId] = useState("");
  const [conversionId, setConversionId] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const signature = evidence.find((item) => item.kind === "signature");

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
    if (delivery.collectionRequired && delivery.collectionAmountDue === null) {
      setMessage(
        "The server-calculated COD due is unavailable. Refresh the Delivery before capture.",
      );
      return;
    }
    if (
      delivery.collectionRequired &&
      settlementMode !== "on_account" &&
      (!isCanonicalPositiveDecimal(cashCollected) ||
        Number(cashCollected) < Number(delivery.collectionAmountDue))
    ) {
      setMessage(
        `Collection must be a positive decimal covering ${delivery.collectionAmountDue}.`,
      );
      return;
    }
    if (
      delivery.collectionRequired &&
      settlementMode === "noncash" &&
      paymentReceiptId.trim().length === 0
    ) {
      setMessage("Enter the cleared non-cash Payment Receipt ID.");
      return;
    }
    if (
      delivery.collectionRequired &&
      settlementMode === "on_account" &&
      conversionId.trim().length === 0
    ) {
      setMessage("Enter the approved On Account conversion ID.");
      return;
    }
    const confirmationId = randomUUID();
    const capturedAt = now();
    await store.saveAndEnqueue(
      {
        command: {
          confirmation_id: confirmationId,
          device_captured_at: capturedAt,
          evidence_ids: evidence.map((item) => item.evidenceId),
          expected_delivery_version: delivery.version,
          lines: delivery.lines.map((line) => ({
            accepted_quantity_base: line.quantityBase,
            line_id: line.lineId,
          })),
          notes: notes.trim() || null,
          recipient_name: recipient.trim(),
          ...(delivery.collectionRequired && settlementMode !== "on_account"
            ? {
                collection: {
                  amount: cashCollected,
                  currency: "PHP",
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
          ...(delivery.collectionRequired && settlementMode === "on_account"
            ? { on_account_conversion_id: conversionId.trim() }
            : {}),
        },
        deliveryId: delivery.deliveryId,
        evidence,
        idempotencyKey: `delivery-confirmation:${confirmationId}`,
      },
      now(),
    );
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
            COD due: PHP {delivery.collectionAmountDue ?? "Unavailable"}
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
            Settlement and proof stay Pending Sync offline and take effect only
            after server acknowledgement.
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
      <Text style={styles.help}>
        Full accepted quantity:{" "}
        {delivery.lines.map((line) => line.quantityBase).join(" · ")}
      </Text>
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
