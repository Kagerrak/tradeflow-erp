import {
  paymentStateContent,
  type PaymentOperationalState,
  type PaymentReceiptCommandState,
} from "@tradeflow/payment-clearance";
import { colors } from "@tradeflow/design-tokens";
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

import type { PaymentReceiptStore } from "../offline/payment-receipt-store";
import { syncPaymentReceipts } from "../offline/payment-receipt-sync";

type Method = "bank_transfer" | "cash" | "check" | "electronic";

export type PaymentReceiptCaptureProps = {
  accessToken: string | undefined;
  baseUrl: string;
  createId?: () => string;
  fetch?: (request: Request) => Promise<Response>;
  isOnline?: () => Promise<boolean>;
  store: PaymentReceiptStore;
};

export function PaymentReceiptCapture({
  accessToken,
  baseUrl,
  createId = randomUUID,
  fetch,
  isOnline = async () => {
    const state = await getNetworkStateAsync();
    return state.isConnected === true && state.isInternetReachable !== false;
  },
  store,
}: PaymentReceiptCaptureProps) {
  const [method, setMethod] = useState<Method>("cash");
  const [branchId, setBranchId] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [salesOrderId, setSalesOrderId] = useState("");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [provider, setProvider] = useState("");
  const [documentUrl, setDocumentUrl] = useState("");
  const [state, setState] = useState<
    PaymentReceiptCommandState | { kind: "idle" | "queued" }
  >({ kind: "idle" });
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const commandIdentity = useRef<{
    fingerprint: string;
    idempotencyKey: string;
    receiptId: string;
  } | null>(null);

  useEffect(() => {
    let active = true;
    void store.listPending().then((pending) => {
      if (active && pending.length > 0) setState({ kind: "queued" });
    });
    return () => {
      active = false;
    };
  }, [store]);

  const syncOutbox = async () => {
    setBusy(true);
    if (!(await isOnline())) {
      setState({ kind: "queued" });
      setBusy(false);
      return;
    }
    const sync = await syncPaymentReceipts({
      accessToken,
      baseUrl,
      createCorrelationId: createId,
      ...(fetch === undefined ? {} : { fetch }),
      store,
    });
    if (sync.kind === "synced" || sync.kind === "paused") {
      setState(sync.state);
    }
    setBusy(false);
  };

  const submit = async () => {
    if (
      branchId.trim() === "" ||
      customerId.trim() === "" ||
      amount.trim() === ""
    ) {
      setMessage("Branch, Customer Account, and received amount are required.");
      return;
    }
    if (
      method !== "cash" &&
      (reference.trim() === "" ||
        provider.trim() === "" ||
        documentUrl.trim() === "")
    ) {
      setMessage(
        "Attach the non-cash reference, provider, and evidence document.",
      );
      return;
    }
    const fingerprint = JSON.stringify({
      amount,
      branchId,
      customerId,
      documentUrl,
      method,
      provider,
      reference,
      salesOrderId,
    });
    if (commandIdentity.current?.fingerprint !== fingerprint) {
      commandIdentity.current = {
        fingerprint,
        idempotencyKey: createId(),
        receiptId: createId(),
      };
    }
    setMessage(null);
    setBusy(true);
    const command = {
      amount,
      branch_id: branchId.trim(),
      currency: "PHP",
      customer_id: customerId.trim(),
      evidence:
        method === "cash"
          ? null
          : {
              account_or_provider: provider.trim(),
              document_url: documentUrl.trim(),
              value_date: new Date().toISOString().slice(0, 10),
            },
      external_reference: method === "cash" ? null : reference.trim(),
      payment_method: method,
      payment_receipt_id: commandIdentity.current.receiptId,
      received_at: new Date().toISOString(),
      sales_order_id: salesOrderId.trim() === "" ? null : salesOrderId.trim(),
    };
    await store.saveAndEnqueue(
      command,
      commandIdentity.current.idempotencyKey,
      new Date().toISOString(),
    );
    setBusy(false);
    await syncOutbox();
  };

  return (
    <ScrollView
      contentContainerStyle={styles.screen}
      contentInsetAdjustmentBehavior="automatic"
    >
      <Text style={styles.eyebrow}>ROUTE COLLECTION / 007</Text>
      <Text accessibilityRole="header" style={styles.title}>
        Capture now. Clear with proof.
      </Text>
      <Text style={styles.intro}>
        Field capture can queue a receipt. Verification, bank clearance, and
        Pick Release stay server-authoritative.
      </Text>

      <View style={styles.methodRail}>
        {(["cash", "bank_transfer", "check", "electronic"] as const).map(
          (value) => (
            <Pressable
              accessibilityRole="button"
              key={value}
              onPress={() => setMethod(value)}
              style={[styles.method, method === value && styles.methodSelected]}
            >
              <Text
                style={[
                  styles.methodText,
                  method === value && styles.methodTextSelected,
                ]}
              >
                {value.replaceAll("_", " ").toUpperCase()}
              </Text>
            </Pressable>
          ),
        )}
      </View>

      <View style={styles.form}>
        <Field label="Branch ID" onChangeText={setBranchId} value={branchId} />
        <Field
          label="Customer Account ID"
          onChangeText={setCustomerId}
          value={customerId}
        />
        <Field
          label="Sales Order ID (optional)"
          onChangeText={setSalesOrderId}
          value={salesOrderId}
        />
        <Field
          keyboardType="decimal-pad"
          label="Received amount"
          onChangeText={setAmount}
          value={amount}
        />
        {method !== "cash" && (
          <>
            <Field
              label="External reference"
              onChangeText={setReference}
              value={reference}
            />
            <Field
              label="Account or provider"
              onChangeText={setProvider}
              value={provider}
            />
            <Field
              label="Evidence document URL"
              onChangeText={setDocumentUrl}
              value={documentUrl}
            />
          </>
        )}
        {message !== null && (
          <Text accessibilityRole="alert" style={styles.error}>
            {message}
          </Text>
        )}
        <Pressable
          accessibilityLabel="Queue payment receipt"
          accessibilityRole="button"
          disabled={busy}
          onPress={() => void submit()}
          style={({ pressed }) => [
            styles.primary,
            pressed && styles.primaryPressed,
            busy && styles.disabled,
          ]}
        >
          <Text style={styles.primaryText}>
            {busy ? "CHECKING CONNECTION…" : "QUEUE PAYMENT RECEIPT"}
          </Text>
        </Pressable>
      </View>

      {state.kind === "queued" && (
        <View accessibilityLiveRegion="polite" style={styles.result}>
          <Text style={styles.resultKicker}>PENDING SYNC</Text>
          <Text accessibilityRole="header" style={styles.resultTitle}>
            Pending sync on this device
          </Text>
          <Text style={styles.resultBody}>
            The durable local outbox retained this receipt for a later sync. It
            is not cleared money until the server acknowledges it. Do not
            release goods from this local state.
          </Text>
          <Pressable
            accessibilityLabel="Sync pending payment receipt"
            accessibilityRole="button"
            disabled={busy}
            onPress={() => void syncOutbox()}
            style={({ pressed }) => [
              styles.syncButton,
              pressed && styles.primaryPressed,
              busy && styles.disabled,
            ]}
          >
            <Text style={styles.syncButtonText}>
              {busy ? "CHECKING CONNECTION…" : "SYNC PENDING RECEIPT"}
            </Text>
          </Pressable>
        </View>
      )}
      {(state.kind === "recorded" || state.kind === "updated") && (
        <View accessibilityLiveRegion="polite" style={styles.result}>
          <Text style={styles.resultKicker}>
            {state.receipt.status.replaceAll("_", " ").toUpperCase()}
          </Text>
          <Text accessibilityRole="header" style={styles.resultTitle}>
            {paymentStateContent[state.receipt.status].title}
          </Text>
          <Text style={styles.resultBody}>
            {paymentStateContent[state.receipt.status].nextAction}
          </Text>
          {state.receipt.cashReconciliationStatus === "unreconciled" && (
            <Text style={styles.cashWarning}>
              Cash reconciliation remains due for this cleared receipt.
            </Text>
          )}
        </View>
      )}

      <Text style={styles.guideTitle}>WHAT THE NEXT STATE MEANS</Text>
      <View style={styles.guide}>
        {(
          [
            "pending_verification",
            "awaiting_bank_clearance",
            "cleared",
            "insufficient",
            "payment_hold",
            "retry_ready",
            "rejected",
            "reversed",
          ] as PaymentOperationalState[]
        ).map((value) => (
          <View key={value} style={styles.guideRow}>
            <Text style={styles.guideState}>
              {paymentStateContent[value].title}
            </Text>
            <Text style={styles.guideAction}>
              {paymentStateContent[value].nextAction}
            </Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

function Field({
  keyboardType,
  label,
  onChangeText,
  value,
}: {
  keyboardType?: "decimal-pad";
  label: string;
  onChangeText: (value: string) => void;
  value: string;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label.toUpperCase()}</Text>
      <TextInput
        accessibilityLabel={label.replace(" (optional)", "")}
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
  cashWarning: {
    backgroundColor: colors.amberSoft,
    color: colors.amber,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 13,
    marginTop: 14,
    padding: 12,
  },
  disabled: { opacity: 0.55 },
  error: {
    backgroundColor: colors.redSoft,
    color: colors.red,
    fontFamily: "IBMPlexSans_500Medium",
    padding: 12,
  },
  field: { gap: 6 },
  form: {
    backgroundColor: colors.paper,
    borderColor: colors.inkMuted,
    borderTopWidth: 1,
    gap: 16,
    paddingVertical: 22,
  },
  guide: { borderColor: colors.inkMuted, borderTopWidth: 1 },
  guideAction: {
    color: colors.inkMuted,
    flex: 1.2,
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
    flex: 0.8,
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
    fontSize: 16,
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
  eyebrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1.3,
    marginBottom: 7,
  },
  label: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 10,
    letterSpacing: 0.9,
  },
  method: {
    alignItems: "center",
    backgroundColor: colors.paperDeep,
    borderColor: colors.inkMuted,
    borderRightWidth: 1,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 7,
  },
  methodRail: {
    borderColor: colors.inkMuted,
    borderWidth: 1,
    flexDirection: "row",
  },
  methodSelected: { backgroundColor: colors.ink },
  methodText: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 9,
  },
  methodTextSelected: { color: colors.paper },
  primary: {
    alignItems: "center",
    backgroundColor: colors.orange,
    borderColor: colors.ink,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 50,
  },
  primaryPressed: { opacity: 0.82 },
  primaryText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 12,
    letterSpacing: 0.7,
  },
  result: {
    backgroundColor: colors.greenSoft,
    borderColor: colors.green,
    borderWidth: 1,
    marginTop: 22,
    padding: 18,
  },
  resultBody: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 14,
    lineHeight: 21,
  },
  resultKicker: {
    color: colors.green,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1,
    marginBottom: 5,
  },
  resultTitle: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 27,
    marginBottom: 7,
  },
  screen: {
    backgroundColor: colors.paper,
    flexGrow: 1,
    paddingBottom: 56,
    paddingHorizontal: 22,
    paddingTop: 42,
  },
  syncButton: {
    alignItems: "center",
    borderColor: colors.green,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 16,
    minHeight: 46,
  },
  syncButtonText: {
    color: colors.green,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 0.6,
  },
  title: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 45,
    letterSpacing: -1.5,
    lineHeight: 45,
    marginBottom: 14,
  },
});
