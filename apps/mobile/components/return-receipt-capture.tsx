import type { components } from "@tradeflow/api-client";
import { colors } from "@tradeflow/design-tokens";
import { CryptoDigestAlgorithm, digest, randomUUID } from "expo-crypto";
import * as ImagePicker from "expo-image-picker";
import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { persistEvidenceFile } from "../offline/durable-evidence-file";
import type {
  LocalReturnEvidence,
  ReturnReceiptStore,
} from "../offline/return-receipt-store";

type ReturnRequest = components["schemas"]["ReturnRequestResponse"];
type LineResponse = components["schemas"]["ReturnRequestLineResponse"];
type Outcome = components["schemas"]["ReturnReceiptLineCommand"]["outcome"];

const OUTCOMES: Outcome[] = ["restock", "quarantine", "damaged", "rejected"];

type LineConfig = {
  notes: string;
  outcome: Outcome;
  quantity: string;
};

export function ReturnReceiptCapture({
  now = () => new Date().toISOString(),
  onSaved,
  persistEvidence = persistEvidenceFile,
  request,
  replacesReceiptId,
  replacementSource,
  store,
}: {
  now?: () => string;
  onSaved: () => void;
  persistEvidence?: (
    sourceUri: string,
    evidenceId: string,
    extension: string,
  ) => Promise<string>;
  request: ReturnRequest;
  replacesReceiptId?: string;
  replacementSource?: {
    command: {
      lines: {
        notes: string | null;
        outcome: Outcome;
        received_quantity_base: number | string;
        return_request_line_id: string;
      }[];
      notes: string | null;
    };
    evidence: LocalReturnEvidence[];
  };
  store: ReturnReceiptStore;
}) {
  const [lineConfigs, setLineConfigs] = useState<Record<string, LineConfig>>(
    () => {
      const configs: Record<string, LineConfig> = {};
      const sourceLines = replacementSource?.command.lines ?? [];
      for (const line of request.lines) {
        const source = sourceLines.find(
          (item) => item.return_request_line_id === line.return_request_line_id,
        );
        configs[line.return_request_line_id] = {
          notes: source?.notes ?? "",
          outcome: source?.outcome ?? "restock",
          quantity:
            typeof source?.received_quantity_base === "string"
              ? source.received_quantity_base
              : line.quantity_base,
        };
      }
      return configs;
    },
  );
  const [notes, setNotes] = useState(
    replacementSource?.command.notes ?? request.notes ?? "",
  );
  const [evidence, setEvidence] = useState<LocalReturnEvidence[]>(
    replacementSource?.evidence ?? [],
  );
  const [message, setMessage] = useState<string | null>(null);

  const capturePhoto = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      setMessage("Camera permission is required to capture Return evidence.");
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
    const next: LocalReturnEvidence = {
      contentType,
      evidenceId,
      kind: "photo",
      localUri,
      sha256: hex(checksum),
      sizeBytes: bytes.byteLength,
      status: "pending_upload",
    };
    setEvidence((current) => [...current, next]);
    setMessage(null);
  };

  const queue = async () => {
    if (evidence.length === 0) {
      setMessage("At least one inspection photo is required.");
      return;
    }
    const lines = buildLineCommands(request.lines, lineConfigs);
    if (lines === null) {
      setMessage("Enter a valid received quantity for every line.");
      return;
    }
    const receiptId = randomUUID();
    const capturedAt = now();
    const capture = {
      command: {
        evidence_ids: evidence.map((item) => item.evidenceId),
        expected_request_version: request.version,
        lines,
        notes: notes.trim() || null,
        received_at: capturedAt,
        return_receipt_id: receiptId,
      },
      evidence,
      idempotencyKey: `return-receipt:${receiptId}`,
      requestId: request.return_request_id,
    };
    if (replacesReceiptId === undefined) {
      await store.saveAndEnqueue(capture, now());
    } else {
      await store.replaceConflict(replacesReceiptId, capture, now());
    }
    onSaved();
  };

  return (
    <View style={styles.panel}>
      <Text style={styles.eyebrow}>RETURN RECEIPT</Text>
      <Text accessibilityRole="header" style={styles.title}>
        Inspect and receive returned stock
      </Text>
      <Text style={styles.help}>Request {request.return_request_id}</Text>
      {request.lines.map((line) => (
        <ReturnLineEditor
          key={line.return_request_line_id}
          config={lineConfigs[line.return_request_line_id]}
          line={line}
          onChange={(patch) =>
            setLineConfigs((current) => ({
              ...current,
              [line.return_request_line_id]: {
                ...current[line.return_request_line_id],
                ...patch,
              } as LineConfig,
            }))
          }
        />
      ))}
      <TextInput
        accessibilityLabel="Receipt notes"
        multiline
        onChangeText={setNotes}
        placeholder="Condition, count, or custody note"
        style={[styles.input, styles.notes]}
        value={notes}
      />
      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          onPress={() => void capturePhoto()}
        >
          <Text style={styles.link}>ADD INSPECTION PHOTO</Text>
        </Pressable>
      </View>
      <Text style={styles.help}>
        {evidence.length} inspection photo{evidence.length === 1 ? "" : "s"}
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

function ReturnLineEditor({
  config,
  line,
  onChange,
}: {
  config: LineConfig | undefined;
  line: LineResponse;
  onChange: (patch: Partial<LineConfig>) => void;
}) {
  if (config === undefined) return null;
  const quantityDisabled = config.outcome === "rejected";
  return (
    <View style={styles.line}>
      <Text style={styles.help}>
        {line.sku_id}: {line.quantity_base} authorized
      </Text>
      <Text accessibilityLabel={`${line.sku_id} outcome`} style={styles.help}>
        Outcome
      </Text>
      <View style={styles.actions}>
        {OUTCOMES.map((value) => (
          <Pressable
            key={value}
            onPress={() =>
              onChange({
                outcome: value,
                quantity: value === "rejected" ? "0" : line.quantity_base,
              })
            }
          >
            <Text style={styles.link}>
              {config.outcome === value ? "● " : "○ "}
              {value.toUpperCase()}
            </Text>
          </Pressable>
        ))}
      </View>
      <TextInput
        accessibilityLabel={`${line.sku_id} received quantity`}
        editable={!quantityDisabled}
        keyboardType="decimal-pad"
        onChangeText={(quantity) => onChange({ quantity })}
        placeholder="0.000000"
        style={styles.input}
        value={quantityDisabled ? "0" : config.quantity}
      />
      <TextInput
        accessibilityLabel={`${line.sku_id} line notes`}
        onChangeText={(lineNotes) => onChange({ notes: lineNotes })}
        placeholder="Line condition or custody note"
        style={styles.input}
        value={config.notes}
      />
    </View>
  );
}

function buildLineCommands(
  lines: LineResponse[],
  configs: Record<string, LineConfig>,
): components["schemas"]["ReturnReceiptLineCommand"][] | null {
  const result: components["schemas"]["ReturnReceiptLineCommand"][] = [];
  for (const line of lines) {
    const config = configs[line.return_request_line_id];
    if (config === undefined) return null;
    const outcome = config.outcome;
    if (outcome === "rejected") {
      result.push({
        notes: config.notes.trim() || null,
        outcome,
        received_quantity_base: "0",
        return_request_line_id: line.return_request_line_id,
      });
      continue;
    }
    if (!isValidQuantity(config.quantity, line.quantity_base)) return null;
    result.push({
      notes: config.notes.trim() || null,
      outcome,
      received_quantity_base: config.quantity,
      return_request_line_id: line.return_request_line_id,
    });
  }
  return result;
}

function isValidQuantity(value: string, maximum: string): boolean {
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(value)) return false;
  const quantity = Number(value);
  const limit = Number(maximum);
  return quantity > 0 && quantity <= limit;
}

function contentTypeFor(
  value: string | undefined,
): LocalReturnEvidence["contentType"] | null {
  if (value === "image/jpeg" || value === "image/png" || value === "image/webp")
    return value;
  return null;
}

function extensionFor(value: LocalReturnEvidence["contentType"]): string {
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
  actions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 16,
    marginVertical: 10,
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
    marginTop: 12,
    paddingVertical: 10,
  },
  line: { marginTop: 18 },
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
