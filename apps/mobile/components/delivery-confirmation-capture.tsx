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
    const confirmationId = randomUUID();
    await store.saveAndEnqueue(
      {
        command: {
          confirmation_id: confirmationId,
          device_captured_at: now(),
          evidence_ids: evidence.map((item) => item.evidenceId),
          expected_delivery_version: delivery.version,
          lines: delivery.lines.map((line) => ({
            accepted_quantity_base: line.quantityBase,
            line_id: line.lineId,
          })),
          notes: notes.trim() || null,
          recipient_name: recipient.trim(),
        },
        deliveryId: delivery.deliveryId,
        evidence,
        idempotencyKey: `delivery-confirmation:${confirmationId}`,
      },
      now(),
    );
    onSaved();
  };

  if (delivery.collectionRequired) {
    return (
      <View style={styles.panel}>
        <Text accessibilityRole="header" style={styles.title}>
          Collection required before confirmation
        </Text>
        <Text style={styles.help}>
          COD acceptance is handled atomically in Issue #11.
        </Text>
      </View>
    );
  }
  return (
    <View style={styles.panel}>
      <Text style={styles.eyebrow}>ACCEPTED DELIVERY</Text>
      <Text accessibilityRole="header" style={styles.title}>
        Capture Proof of Delivery
      </Text>
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
        <Text style={styles.submitText}>SAVE TO PENDING SYNC</Text>
      </Pressable>
    </View>
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
