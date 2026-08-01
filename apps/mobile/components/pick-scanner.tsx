import { colors } from "@tradeflow/design-tokens";
import { createTradeFlowClient } from "@tradeflow/api-client";
import { randomUUID } from "expo-crypto";
import { getNetworkStateAsync } from "expo-network";
import { useEffect, useRef, useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type {
  LocalPickCommand,
  LocalPickStatus,
  PickCommand,
  PickCommandStore,
} from "../offline/pick-command-store";
import {
  reverseSyncedPick,
  syncPickCommands,
} from "../offline/pick-command-sync";

export type PickScannerProps = {
  accessToken: string | undefined;
  baseUrl: string;
  createId?: () => string;
  fetch?: (request: Request) => Promise<Response>;
  isOnline?: () => Promise<boolean>;
  store: PickCommandStore;
};

const stateContent: Record<
  LocalPickStatus,
  {
    body: string;
    kicker: string;
    title: string;
    tone: "error" | "success" | "warning";
  }
> = {
  complete: {
    body: "The server posted the full released quantity into Dispatch Staging.",
    kicker: "SERVER ACKNOWLEDGED",
    title: "Pick complete",
    tone: "success",
  },
  conflict: {
    body: "Server state changed after this command was captured. Review the current Fulfillment Order before scanning again.",
    kicker: "AUTHORITATIVE CONFLICT",
    title: "Pick needs review",
    tone: "warning",
  },
  forbidden: {
    body: "Your current capability or warehouse scope does not authorize this Pick. No stock moved.",
    kicker: "AUTHORITY DENIED",
    title: "Picking access denied",
    tone: "error",
  },
  partially_picked: {
    body: "Only the acknowledged quantity moved into Dispatch Staging. The remainder stays released for picking.",
    kicker: "SERVER ACKNOWLEDGED",
    title: "Partial pick staged",
    tone: "success",
  },
  pending_sync: {
    body: "The durable outbox retained this command. Available stock has not moved until the server acknowledges the Pick.",
    kicker: "DEVICE OUTBOX",
    title: "Pending Sync — not staged",
    tone: "warning",
  },
  reversed: {
    body: "The server reports this Pick as reversed. The original Pick evidence remains in history; reload before new work.",
    kicker: "REVERSAL RECORDED",
    title: "Pick reversed",
    tone: "warning",
  },
  scan_denied: {
    body: "The barcode or tracked identity is not eligible for this released line. No stock moved; scan an eligible identity.",
    kicker: "IDENTITY REJECTED",
    title: "Scan denied",
    tone: "error",
  },
};

export function PickScanner({
  accessToken,
  baseUrl,
  createId = randomUUID,
  fetch,
  isOnline = async () => {
    const state = await getNetworkStateAsync();
    return state.isConnected === true && state.isInternetReachable !== false;
  },
  store,
}: PickScannerProps) {
  const [barcode, setBarcode] = useState("");
  const [busy, setBusy] = useState(false);
  const [capture, setCapture] = useState<LocalPickCommand | null>(null);
  const [expectedVersion, setExpectedVersion] = useState("");
  const [fulfillmentOrderId, setFulfillmentOrderId] = useState("");
  const [lineId, setLineId] = useState("");
  const [lotCode, setLotCode] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const [manualReason, setManualReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [quantity, setQuantity] = useState("");
  const [queueState, setQueueState] = useState<
    "empty" | "error" | "idle" | "loading" | "ready"
  >("idle");
  const [reversalReason, setReversalReason] = useState("");
  const [serialNumber, setSerialNumber] = useState("");
  const [unitCode, setUnitCode] = useState("EA");
  const [fefoReason, setFefoReason] = useState("");
  const commandIdentity = useRef<{
    fingerprint: string;
    idempotencyKey: string;
    pickId: string;
  } | null>(null);
  const reversalIdentity = useRef<{
    idempotencyKey: string;
    pickId: string;
  } | null>(null);

  const applyCapture = (next: LocalPickCommand | null) => {
    setCapture(next);
    if (next === null) return;
    const line = next.command.lines[0];
    setFulfillmentOrderId(next.fulfillmentOrderId);
    setExpectedVersion(String(next.command.expected_fulfillment_version));
    if (line !== undefined) {
      const selection = line.selections[0];
      setBarcode(selection?.barcode ?? "");
      setFefoReason(selection?.fefo_override_reason ?? "");
      setLineId(line.line_id);
      setLotCode(selection?.lot_code ?? "");
      setManualMode(
        selection?.barcode === undefined && selection !== undefined,
      );
      setManualReason(selection?.manual_reason ?? "");
      setQuantity(String(line.quantity));
      setSerialNumber(selection?.serial_number ?? "");
      setUnitCode(line.unit_code);
    }
  };

  const hydrateLatest = async () => {
    const captures = await store.listCaptures();
    applyCapture(captures[0] ?? null);
  };

  useEffect(() => {
    let active = true;
    void store.listCaptures().then((captures) => {
      if (active) applyCapture(captures[0] ?? null);
    });
    return () => {
      active = false;
    };
  }, [store]);

  const syncOutbox = async () => {
    setBusy(true);
    if (!(await isOnline())) {
      await hydrateLatest();
      setBusy(false);
      return;
    }
    await syncPickCommands({
      accessToken,
      baseUrl,
      createCorrelationId: createId,
      ...(fetch === undefined ? {} : { fetch }),
      store,
    });
    await hydrateLatest();
    setBusy(false);
  };

  const loadReleasedWork = async () => {
    if (accessToken === undefined || accessToken === "") {
      setQueueState("error");
      setMessage("Sign in before loading released Warehouse work.");
      return;
    }
    setQueueState("loading");
    setMessage(null);
    const correlationId = createId();
    const client = createTradeFlowClient({
      accessToken,
      baseUrl,
      correlationId,
      ...(fetch === undefined ? {} : { fetch }),
    });
    try {
      const orders = await client.GET("/v1/fulfillment/orders", {
        params: { query: {} },
      });
      if (orders.data === undefined) {
        setQueueState("error");
        setMessage("Released Warehouse work could not be loaded.");
        return;
      }
      const next = orders.data.items.find((item) =>
        ["pick_released", "partially_picked"].includes(item.status),
      );
      if (next === undefined) {
        setQueueState("empty");
        return;
      }
      const context = await client.GET(
        "/v1/fulfillment/orders/{fulfillment_order_id}/picking-context",
        {
          params: {
            path: { fulfillment_order_id: next.fulfillment_order_id },
          },
        },
      );
      const line = context.data?.lines[0];
      if (context.data === undefined || line === undefined) {
        setQueueState("empty");
        return;
      }
      setFulfillmentOrderId(context.data.fulfillment_order_id);
      setExpectedVersion(String(context.data.version));
      setLineId(line.line_id);
      setQuantity(line.remaining_quantity_base);
      setUnitCode(line.base_stocking_unit);
      setQueueState("ready");
    } catch {
      setQueueState("error");
      setMessage("Released Warehouse work could not be reached.");
    }
  };

  const submit = async () => {
    const parsedVersion = Number(expectedVersion);
    if (
      fulfillmentOrderId.trim() === "" ||
      lineId.trim() === "" ||
      quantity.trim() === "" ||
      unitCode.trim() === "" ||
      !Number.isInteger(parsedVersion) ||
      parsedVersion < 1
    ) {
      setMessage(
        "Fulfillment Order, line, released version, quantity, and unit are required.",
      );
      return;
    }
    if (
      manualMode &&
      (manualReason.trim() === "" ||
        (lotCode.trim() === "" && serialNumber.trim() === ""))
    ) {
      setMessage("Manual fallback requires a tracked identity and reason.");
      return;
    }
    const fingerprint = JSON.stringify({
      barcode: barcode.trim(),
      expectedVersion: parsedVersion,
      fulfillmentOrderId: fulfillmentOrderId.trim(),
      lineId: lineId.trim(),
      lotCode: lotCode.trim(),
      manualMode,
      manualReason: manualReason.trim(),
      quantity: quantity.trim(),
      serialNumber: serialNumber.trim(),
      unitCode: unitCode.trim().toUpperCase(),
    });
    const terminalCapture =
      capture !== null &&
      ["conflict", "forbidden", "reversed", "scan_denied"].includes(
        capture.status,
      );
    if (
      commandIdentity.current?.fingerprint !== fingerprint ||
      terminalCapture
    ) {
      commandIdentity.current = {
        fingerprint,
        pickId: createId(),
        idempotencyKey: createId(),
      };
    }
    const command: PickCommand = {
      expected_fulfillment_version: parsedVersion,
      lines: [
        {
          line_id: lineId.trim(),
          quantity: quantity.trim(),
          selections: manualMode
            ? [
                {
                  ...(fefoReason.trim() === ""
                    ? {}
                    : { fefo_override_reason: fefoReason.trim() }),
                  ...(lotCode.trim() === ""
                    ? {}
                    : { lot_code: lotCode.trim() }),
                  manual_reason: manualReason.trim(),
                  quantity: quantity.trim(),
                  ...(serialNumber.trim() === ""
                    ? {}
                    : { serial_number: serialNumber.trim() }),
                },
              ]
            : barcode.trim() === ""
              ? []
              : [{ barcode: barcode.trim() }],
          unit_code: unitCode.trim().toUpperCase(),
        },
      ],
      pick_id: commandIdentity.current.pickId,
    };
    setMessage(null);
    setBusy(true);
    const saved = await store.saveAndEnqueue(
      fulfillmentOrderId.trim(),
      command,
      commandIdentity.current.idempotencyKey,
      new Date().toISOString(),
    );
    applyCapture(saved);
    setBusy(false);
    await syncOutbox();
  };

  const reverse = async () => {
    if (capture?.response === null || capture?.response === undefined) return;
    if (reversalReason.trim() === "") {
      setMessage("A reversal reason is required.");
      return;
    }
    if (!(await isOnline())) {
      setMessage("Reversal requires an online authoritative confirmation.");
      return;
    }
    reversalIdentity.current ??= {
      idempotencyKey: createId(),
      pickId: createId(),
    };
    setBusy(true);
    const result = await reverseSyncedPick({
      accessToken,
      baseUrl,
      createCorrelationId: createId,
      expectedVersion: capture.response.version,
      ...(fetch === undefined ? {} : { fetch }),
      idempotencyKey: reversalIdentity.current.idempotencyKey,
      pickId: capture.pickId,
      reason: reversalReason.trim(),
      reversalPickId: reversalIdentity.current.pickId,
      store,
    });
    if (result.kind === "reversed") {
      setReversalReason("");
      await hydrateLatest();
    } else {
      setMessage(
        result.reason === "unavailable"
          ? "Reversal response is uncertain; retry with the same command identity."
          : `Reversal paused: ${result.reason}.`,
      );
    }
    setBusy(false);
  };

  return (
    <ScrollView
      contentContainerStyle={styles.screen}
      contentInsetAdjustmentBehavior="automatic"
    >
      <Text style={styles.eyebrow}>WAREHOUSE PICK / 008</Text>
      <Text accessibilityRole="header" style={styles.title}>
        Scan into the pick.
      </Text>
      <Text style={styles.intro}>
        Capture the physical identity first. Dispatch Staging changes only after
        server acknowledgement.
      </Text>

      <Pressable
        accessibilityLabel="Load released Warehouse work"
        accessibilityRole="button"
        disabled={busy || queueState === "loading"}
        onPress={() => void loadReleasedWork()}
        style={({ pressed }) => [
          styles.secondary,
          styles.queueButton,
          pressed && styles.pressed,
        ]}
      >
        <Text style={styles.secondaryText}>
          {queueState === "loading"
            ? "LOADING RELEASED WORK…"
            : "LOAD RELEASED WORK"}
        </Text>
      </Pressable>
      {queueState === "empty" && (
        <View
          accessibilityLiveRegion="polite"
          style={[styles.state, styles.stateWarning]}
        >
          <Text style={styles.stateKicker}>WAREHOUSE QUEUE</Text>
          <Text accessibilityRole="header" style={styles.stateTitle}>
            No released picks
          </Text>
          <Text style={styles.stateBody}>
            This Warehouse scope has no Pick-Released or Partially Picked work.
          </Text>
        </View>
      )}

      <View style={styles.scanDeck}>
        <Text style={styles.scanIndex}>SCAN / 01</Text>
        <Text style={styles.scanLabel}>BARCODE OR TRACKED IDENTITY</Text>
        <TextInput
          accessibilityLabel="Scan barcode"
          autoCapitalize="characters"
          autoCorrect={false}
          onChangeText={setBarcode}
          placeholder="Ready for scanner"
          placeholderTextColor={colors.inkMuted}
          returnKeyType="next"
          style={styles.scanInput}
          value={barcode}
        />
        <Text style={styles.scanHint}>
          Leave blank only for an untracked released line.
        </Text>
      </View>

      <View style={styles.form}>
        <Pressable
          accessibilityLabel="Toggle authorized manual fallback"
          accessibilityRole="button"
          onPress={() => setManualMode((value) => !value)}
          style={({ pressed }) => [styles.secondary, pressed && styles.pressed]}
        >
          <Text style={styles.secondaryText}>
            {manualMode ? "RETURN TO BARCODE SCAN" : "USE MANUAL FALLBACK"}
          </Text>
        </Pressable>
        {manualMode && (
          <View style={styles.manualPanel}>
            <Text style={styles.scanLabel}>AUTHORIZED IDENTITY FALLBACK</Text>
            <Field
              label="Lot identity"
              onChangeText={setLotCode}
              value={lotCode}
            />
            <Field
              label="Serial identity"
              onChangeText={setSerialNumber}
              value={serialNumber}
            />
            <Field
              label="Manual selection reason"
              onChangeText={setManualReason}
              value={manualReason}
            />
            <Field
              label="FEFO override reason (later lot only)"
              onChangeText={setFefoReason}
              value={fefoReason}
            />
          </View>
        )}
        <Field
          label="Fulfillment Order ID"
          onChangeText={setFulfillmentOrderId}
          value={fulfillmentOrderId}
        />
        <Field
          label="Sales Order Line ID"
          onChangeText={setLineId}
          value={lineId}
        />
        <View style={styles.splitFields}>
          <Field
            keyboardType="number-pad"
            label="Released version"
            onChangeText={setExpectedVersion}
            value={expectedVersion}
          />
          <Field
            keyboardType="decimal-pad"
            label="Pick quantity"
            onChangeText={setQuantity}
            value={quantity}
          />
          <Field label="Unit" onChangeText={setUnitCode} value={unitCode} />
        </View>
        {message !== null && (
          <Text accessibilityRole="alert" style={styles.formError}>
            {message}
          </Text>
        )}
        <Pressable
          accessibilityLabel="Queue Pick command"
          accessibilityRole="button"
          disabled={busy}
          onPress={() => void submit()}
          style={({ pressed }) => [
            styles.primary,
            pressed && styles.pressed,
            busy && styles.disabled,
          ]}
        >
          <Text style={styles.primaryText}>
            {busy ? "CHECKING CONNECTION…" : "QUEUE PICK COMMAND"}
          </Text>
        </Pressable>
      </View>

      {capture !== null && (
        <>
          <PickState capture={capture} busy={busy} sync={syncOutbox} />
          {capture.response !== null && capture.status !== "reversed" && (
            <View style={styles.reversalPanel}>
              <Text style={styles.scanLabel}>LINKED PICK REVERSAL</Text>
              <Field
                label="Reversal reason"
                onChangeText={setReversalReason}
                value={reversalReason}
              />
              <Pressable
                accessibilityLabel="Reverse acknowledged Pick"
                accessibilityRole="button"
                disabled={busy}
                onPress={() => void reverse()}
                style={({ pressed }) => [
                  styles.secondary,
                  pressed && styles.pressed,
                  busy && styles.disabled,
                ]}
              >
                <Text style={styles.secondaryText}>REVERSE STAGED CUSTODY</Text>
              </Pressable>
            </View>
          )}
        </>
      )}

      <Text style={styles.guideTitle}>OPERATIONAL STATES</Text>
      <View style={styles.guide}>
        {(
          [
            "pending_sync",
            "scan_denied",
            "conflict",
            "forbidden",
            "partially_picked",
            "complete",
            "reversed",
          ] as LocalPickStatus[]
        ).map((status) => (
          <View key={status} style={styles.guideRow}>
            <Text style={styles.guideState}>{stateContent[status].title}</Text>
            <Text style={styles.guideBody}>{stateContent[status].body}</Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

function PickState({
  busy,
  capture,
  sync,
}: {
  busy: boolean;
  capture: LocalPickCommand;
  sync: () => Promise<void>;
}) {
  const content = stateContent[capture.status];
  const toneStyle =
    content.tone === "success"
      ? styles.stateSuccess
      : content.tone === "error"
        ? styles.stateError
        : styles.stateWarning;
  return (
    <View accessibilityLiveRegion="polite" style={[styles.state, toneStyle]}>
      <Text style={styles.stateKicker}>{content.kicker}</Text>
      <Text accessibilityRole="header" style={styles.stateTitle}>
        {content.title}
      </Text>
      <Text style={styles.stateBody}>{content.body}</Text>
      {capture.status === "partially_picked" && capture.response !== null && (
        <Text style={styles.quantityCallout}>
          {capture.response.remaining_quantity_base} base units remain
        </Text>
      )}
      {capture.status === "complete" && capture.response !== null && (
        <Text style={styles.quantityCallout}>
          {capture.response.picked_quantity_base} base units staged
        </Text>
      )}
      {capture.correlationId !== null && (
        <Text selectable style={styles.supportReference}>
          Support reference {capture.correlationId}
        </Text>
      )}
      {capture.status === "pending_sync" && (
        <Pressable
          accessibilityLabel="Sync pending Pick"
          accessibilityRole="button"
          disabled={busy}
          onPress={() => void sync()}
          style={({ pressed }) => [
            styles.syncButton,
            pressed && styles.pressed,
            busy && styles.disabled,
          ]}
        >
          <Text style={styles.syncText}>
            {busy ? "CHECKING CONNECTION…" : "SYNC SAME COMMAND"}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

function Field({
  keyboardType,
  label,
  onChangeText,
  value,
}: {
  keyboardType?: "decimal-pad" | "number-pad";
  label: string;
  onChangeText: (value: string) => void;
  value: string;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label.toUpperCase()}</Text>
      <TextInput
        accessibilityLabel={label}
        autoCapitalize="none"
        keyboardType={keyboardType}
        onChangeText={onChangeText}
        style={styles.input}
        value={value}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  disabled: { opacity: 0.55 },
  eyebrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1.3,
    marginBottom: 7,
  },
  field: { flex: 1, gap: 6 },
  form: {
    borderTopColor: colors.ink,
    borderTopWidth: 1,
    gap: 16,
    paddingTop: 22,
  },
  formError: {
    backgroundColor: colors.redSoft,
    color: colors.red,
    fontFamily: "IBMPlexSans_500Medium",
    padding: 12,
  },
  guide: { borderTopColor: colors.inkMuted, borderTopWidth: 1 },
  guideBody: {
    color: colors.inkMuted,
    flex: 1.35,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 13,
    lineHeight: 19,
  },
  guideRow: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: 16,
    paddingVertical: 14,
  },
  guideState: {
    color: colors.ink,
    flex: 0.65,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 13,
  },
  guideTitle: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1.2,
    marginBottom: 10,
    marginTop: 34,
  },
  input: {
    backgroundColor: colors.paperDeep,
    borderColor: colors.inkMuted,
    borderWidth: 1,
    color: colors.ink,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 15,
    minHeight: 46,
    paddingHorizontal: 12,
  },
  intro: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 16,
    lineHeight: 24,
    marginBottom: 24,
  },
  label: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 10,
    letterSpacing: 0.8,
  },
  manualPanel: {
    backgroundColor: colors.ink,
    gap: 14,
    padding: 16,
  },
  pressed: { opacity: 0.82 },
  primary: {
    alignItems: "center",
    backgroundColor: colors.orange,
    borderColor: colors.ink,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 52,
  },
  primaryText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 12,
    letterSpacing: 0.8,
  },
  quantityCallout: {
    borderTopColor: colors.inkMuted,
    borderTopWidth: 1,
    color: colors.ink,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 14,
    marginTop: 14,
    paddingTop: 12,
  },
  queueButton: { marginBottom: 20 },
  scanDeck: {
    backgroundColor: colors.ink,
    marginBottom: 28,
    padding: 18,
  },
  scanHint: {
    color: colors.paperDeep,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 12,
    marginTop: 9,
  },
  scanIndex: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1.2,
  },
  scanInput: {
    backgroundColor: colors.paper,
    borderColor: colors.orange,
    borderWidth: 2,
    color: colors.ink,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 20,
    letterSpacing: 0.5,
    minHeight: 58,
    paddingHorizontal: 14,
  },
  scanLabel: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 11,
    letterSpacing: 0.8,
    marginBottom: 9,
    marginTop: 6,
  },
  secondary: {
    alignItems: "center",
    borderColor: colors.ink,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 12,
  },
  secondaryText: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 0.7,
  },
  screen: {
    backgroundColor: colors.paper,
    flexGrow: 1,
    paddingBottom: 56,
    paddingHorizontal: 22,
    paddingTop: 42,
  },
  splitFields: { flexDirection: "row", gap: 10 },
  reversalPanel: {
    backgroundColor: colors.amberSoft,
    borderColor: colors.amber,
    borderWidth: 1,
    gap: 12,
    marginTop: 18,
    padding: 16,
  },
  state: { borderWidth: 1, marginTop: 24, padding: 18 },
  stateBody: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 14,
    lineHeight: 21,
  },
  stateError: { backgroundColor: colors.redSoft, borderColor: colors.red },
  stateKicker: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1,
    marginBottom: 5,
  },
  stateSuccess: {
    backgroundColor: colors.greenSoft,
    borderColor: colors.green,
  },
  stateTitle: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 27,
    marginBottom: 7,
  },
  stateWarning: {
    backgroundColor: colors.amberSoft,
    borderColor: colors.amber,
  },
  supportReference: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 11,
    marginTop: 14,
  },
  syncButton: {
    alignItems: "center",
    borderColor: colors.ink,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 16,
    minHeight: 46,
  },
  syncText: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 0.6,
  },
  title: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 42,
    letterSpacing: -1.2,
    lineHeight: 44,
    marginBottom: 10,
  },
});
