import {
  searchCustomerDirectory,
  type CustomerDirectoryItem,
} from "@tradeflow/customer-directory";
import { colors } from "@tradeflow/design-tokens";
import {
  loadOrderEntryReference,
  type OrderEntryReference,
} from "@tradeflow/sales-order-draft";
import { randomUUID } from "expo-crypto";
import { getNetworkStateAsync } from "expo-network";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type {
  LocalSalesDraft,
  SalesDraftStore,
} from "../offline/sales-draft-store";
import { syncSalesDrafts } from "../offline/sales-draft-sync";

export type SalesOrderDraftCaptureProps = {
  accessToken: string | undefined;
  baseUrl: string;
  createId?: () => string;
  fetch?: (request: Request) => Promise<Response>;
  store: SalesDraftStore;
};

type ScreenState =
  | { kind: "loading" }
  | {
      customers: CustomerDirectoryItem[];
      kind: "ready";
      offline: boolean;
      reference: OrderEntryReference | null;
    }
  | {
      correlationId: string;
      kind: "forbidden" | "unauthenticated" | "unavailable";
    };

export function SalesOrderDraftCapture({
  accessToken,
  baseUrl,
  createId = randomUUID,
  fetch,
  store,
}: SalesOrderDraftCaptureProps) {
  const [state, setState] = useState<ScreenState>({ kind: "loading" });
  const [selectedCustomer, setSelectedCustomer] =
    useState<CustomerDirectoryItem | null>(null);
  const [quantity, setQuantity] = useState("");
  const [discount, setDiscount] = useState("0.00");
  const [selectedAddressId, setSelectedAddressId] = useState("");
  const [selectedItemId, setSelectedItemId] = useState("");
  const [paymentPolicy, setPaymentPolicy] = useState<
    "prepaid" | "cash_on_delivery" | "on_account"
  >("prepaid");
  const [paymentOverrideReason, setPaymentOverrideReason] = useState("");
  const [overridePrice, setOverridePrice] = useState("");
  const [priceOverrideReason, setPriceOverrideReason] = useState("");
  const [validationMessage, setValidationMessage] = useState<string | null>(
    null,
  );
  const [localDraft, setLocalDraft] = useState<LocalSalesDraft | null>(null);
  const [syncing, setSyncing] = useState(false);
  const orderId = useRef(createId());
  const lineId = useRef(createId());

  const loadCustomers = useCallback(async () => {
    await store.initialize();
    const persisted = await store.listDrafts();
    if (persisted[0] !== undefined) {
      setLocalDraft(persisted[0]);
      orderId.current = persisted[0].orderId;
    }
    const result = await searchCustomerDirectory({
      accessToken,
      baseUrl,
      correlationId: createId(),
      ...(fetch === undefined ? {} : { fetch }),
      query: "",
    });
    if (result.kind === "ready") {
      setState({
        customers: result.items.filter((item) => item.status === "active"),
        kind: "ready",
        offline: false,
        reference: null,
      });
      return;
    }
    if (persisted[0] !== undefined) {
      setState({
        customers: [],
        kind: "ready",
        offline: true,
        reference: null,
      });
      return;
    }
    setState({
      correlationId: result.correlationId,
      kind: result.kind === "validation" ? "unavailable" : result.kind,
    });
  }, [accessToken, baseUrl, createId, fetch, store]);

  useEffect(() => {
    const timer = setTimeout(() => void loadCustomers(), 0);
    return () => clearTimeout(timer);
  }, [loadCustomers]);

  const chooseCustomer = async (customer: CustomerDirectoryItem) => {
    setSelectedCustomer(customer);
    setQuantity("");
    const cacheKey = `${customer.branchId}:${customer.customerId}`;
    const result = await loadOrderEntryReference({
      accessToken,
      baseUrl,
      branchId: customer.branchId,
      correlationId: createId(),
      customerId: customer.customerId,
      ...(fetch === undefined ? {} : { fetch }),
    });
    if (result.kind === "ready") {
      await store.saveReference(
        cacheKey,
        result.reference,
        new Date().toISOString(),
      );
      setState((current) =>
        current.kind === "ready"
          ? {
              ...current,
              offline: false,
              reference: result.reference,
            }
          : current,
      );
      setSelectedAddressId(
        result.reference.addresses[0]?.addressVersionId ?? "",
      );
      setSelectedItemId(result.reference.items[0]?.priceListLineId ?? "");
      setPaymentPolicy(result.reference.paymentTimingDefault);
      return;
    }
    const cached = await store.loadReference(cacheKey);
    if (cached !== null) {
      setState((current) =>
        current.kind === "ready"
          ? { ...current, offline: true, reference: cached }
          : current,
      );
      return;
    }
    setState({
      correlationId: result.correlationId,
      kind:
        result.kind === "validation" || result.kind === "conflict"
          ? "unavailable"
          : result.kind,
    });
  };

  const save = async () => {
    if (
      state.kind !== "ready" ||
      state.reference === null ||
      selectedCustomer === null
    ) {
      return;
    }
    const item = state.reference.items.find(
      (candidate) => candidate.priceListLineId === selectedItemId,
    );
    const address = state.reference.addresses.find(
      (candidate) => candidate.addressVersionId === selectedAddressId,
    );
    if (item === undefined || address === undefined || Number(quantity) <= 0) {
      setValidationMessage(
        "Choose a delivery address and priced item, then enter a positive quantity.",
      );
      return;
    }
    if (
      paymentPolicy !== state.reference.paymentTimingDefault &&
      paymentOverrideReason.trim().length === 0
    ) {
      setValidationMessage("A payment timing override requires a reason.");
      return;
    }
    if (
      (overridePrice.length > 0 && priceOverrideReason.trim().length === 0) ||
      (overridePrice.length === 0 && priceOverrideReason.trim().length > 0)
    ) {
      setValidationMessage(
        "A price override and its reason are required together.",
      );
      return;
    }
    setValidationMessage(null);
    const command = {
      branch_id: selectedCustomer.branchId,
      customer_id: selectedCustomer.customerId,
      expected_customer_version: state.reference.customerVersion,
      expected_price_list_version_id: state.reference.priceListVersionId,
      expected_pricing_date: state.reference.pricingDate,
      delivery_address_version_id: address.addressVersionId,
      lines: [
        {
          line_id: lineId.current,
          expected_price_list_line_id: item.priceListLineId,
          expected_unit_conversion_id: item.unitConversionId,
          expected_unit_conversion_version: item.unitConversionVersion,
          manual_override_unit_price:
            overridePrice.length === 0 ? null : overridePrice,
          price_override_reason:
            overridePrice.length === 0 ? null : priceOverrideReason,
          quantity,
          sku_id: item.skuId,
          unit_code: item.unitCode,
        },
      ],
      order_discount_amount: discount,
      payment_timing_override_reason:
        paymentPolicy === state.reference.paymentTimingDefault
          ? null
          : paymentOverrideReason,
      payment_timing_policy: paymentPolicy,
      sales_order_id: orderId.current,
    };
    setLocalDraft(
      await store.saveAndEnqueue(command, createId(), new Date().toISOString()),
    );
  };

  const synchronize = async () => {
    setSyncing(true);
    const network = await getNetworkStateAsync();
    if (network.isConnected !== true || network.isInternetReachable === false) {
      setState((previous) =>
        previous.kind === "ready" ? { ...previous, offline: true } : previous,
      );
      setSyncing(false);
      return;
    }
    const result = await syncSalesDrafts({
      accessToken,
      baseUrl,
      createCorrelationId: createId,
      ...(fetch === undefined ? {} : { fetch }),
      store,
    });
    const current = await store.load(orderId.current);
    setLocalDraft(current);
    setSyncing(false);
    if (result.kind === "paused" && result.reason === "unavailable") {
      setState((previous) =>
        previous.kind === "ready" ? { ...previous, offline: true } : previous,
      );
    }
  };

  const resolveConflict = async () => {
    if (localDraft?.status !== "conflict" || localDraft.savedDraft === null) {
      return;
    }
    await store.retryConflict(
      localDraft.orderId,
      localDraft.command,
      localDraft.savedDraft.version,
      createId(),
      new Date().toISOString(),
    );
    setLocalDraft(await store.load(localDraft.orderId));
  };

  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Text style={styles.eyebrow}>FIELD SALES / 005</Text>
      <Text accessibilityRole="header" style={styles.title}>
        Draft now. Commit when TradeFlow answers.
      </Text>
      <Text style={styles.copy}>
        Pricing is snapshotted from the server. Pending Sync does not consume
        stock or customer credit.
      </Text>
      <SalesCaptureState
        chooseCustomer={(customer) => void chooseCustomer(customer)}
        localDraft={localDraft}
        quantity={quantity}
        discount={discount}
        overridePrice={overridePrice}
        paymentOverrideReason={paymentOverrideReason}
        paymentPolicy={paymentPolicy}
        priceOverrideReason={priceOverrideReason}
        retry={() => {
          setState({ kind: "loading" });
          void loadCustomers();
        }}
        resolveConflict={() => void resolveConflict()}
        save={() => void save()}
        selectedCustomer={selectedCustomer}
        selectedAddressId={selectedAddressId}
        selectedItemId={selectedItemId}
        setDiscount={setDiscount}
        setOverridePrice={setOverridePrice}
        setPaymentOverrideReason={setPaymentOverrideReason}
        setPaymentPolicy={setPaymentPolicy}
        setPriceOverrideReason={setPriceOverrideReason}
        setQuantity={setQuantity}
        setSelectedAddressId={setSelectedAddressId}
        setSelectedItemId={setSelectedItemId}
        state={state}
        synchronize={() => void synchronize()}
        syncing={syncing}
        validationMessage={validationMessage}
      />
    </ScrollView>
  );
}

function SalesCaptureState({
  chooseCustomer,
  discount,
  localDraft,
  overridePrice,
  paymentOverrideReason,
  paymentPolicy,
  priceOverrideReason,
  quantity,
  retry,
  resolveConflict,
  save,
  selectedCustomer,
  selectedAddressId,
  selectedItemId,
  setDiscount,
  setOverridePrice,
  setPaymentOverrideReason,
  setPaymentPolicy,
  setPriceOverrideReason,
  setQuantity,
  setSelectedAddressId,
  setSelectedItemId,
  state,
  synchronize,
  syncing,
  validationMessage,
}: {
  chooseCustomer: (customer: CustomerDirectoryItem) => void;
  discount: string;
  localDraft: LocalSalesDraft | null;
  overridePrice: string;
  paymentOverrideReason: string;
  paymentPolicy: "prepaid" | "cash_on_delivery" | "on_account";
  priceOverrideReason: string;
  quantity: string;
  retry: () => void;
  resolveConflict: () => void;
  save: () => void;
  selectedCustomer: CustomerDirectoryItem | null;
  selectedAddressId: string;
  selectedItemId: string;
  setDiscount: (value: string) => void;
  setOverridePrice: (value: string) => void;
  setPaymentOverrideReason: (value: string) => void;
  setPaymentPolicy: (
    value: "prepaid" | "cash_on_delivery" | "on_account",
  ) => void;
  setPriceOverrideReason: (value: string) => void;
  setQuantity: (value: string) => void;
  setSelectedAddressId: (value: string) => void;
  setSelectedItemId: (value: string) => void;
  state: ScreenState;
  synchronize: () => void;
  syncing: boolean;
  validationMessage: string | null;
}) {
  if (state.kind === "loading") {
    return (
      <View accessibilityRole="progressbar" style={styles.panel}>
        <ActivityIndicator color={colors.orange} />
        <Text style={styles.panelTitle}>Loading customer scope…</Text>
      </View>
    );
  }
  if (state.kind !== "ready") {
    return (
      <View style={styles.panel}>
        <Text accessibilityRole="header" style={styles.panelTitle}>
          {state.kind === "forbidden"
            ? "Sales access is not assigned"
            : state.kind === "unauthenticated"
              ? "Sign in to capture Sales Orders"
              : "Sales capture is unavailable"}
        </Text>
        <Text selectable style={styles.reference}>
          Support reference {state.correlationId}
        </Text>
        {state.kind === "unavailable" && (
          <Pressable
            accessibilityRole="button"
            onPress={retry}
            style={styles.button}
          >
            <Text style={styles.buttonText}>RETRY</Text>
          </Pressable>
        )}
      </View>
    );
  }
  if (state.customers.length === 0 && localDraft === null) {
    return (
      <View style={styles.panel}>
        <Text accessibilityRole="header" style={styles.panelTitle}>
          No active Customer Accounts
        </Text>
        <Text style={styles.copy}>
          Ask sales administration to assign an active account in your Branch.
        </Text>
      </View>
    );
  }
  return (
    <View style={styles.panel}>
      {state.offline && (
        <Text style={styles.pending}>OFFLINE / CACHED PRICING</Text>
      )}
      <Text style={styles.label}>CUSTOMER ACCOUNT</Text>
      {state.customers.map((customer) => (
        <Pressable
          accessibilityLabel={`Select ${customer.accountNumber}`}
          accessibilityRole="button"
          key={customer.customerId}
          onPress={() => chooseCustomer(customer)}
          style={[
            styles.customer,
            selectedCustomer?.customerId === customer.customerId &&
              styles.customerSelected,
          ]}
        >
          <Text style={styles.customerCode}>{customer.accountNumber}</Text>
          <Text style={styles.copy}>{customer.legalName}</Text>
        </Pressable>
      ))}
      {state.reference !== null && (
        <View>
          <Text style={styles.reference}>
            {state.reference.priceListCode} / V
            {state.reference.priceListVersion} /{" "}
            {state.reference.priceInclusionMode.toUpperCase()}
          </Text>
          <Text style={styles.label}>DELIVERY ADDRESS</Text>
          {state.reference.addresses.map((address) => (
            <Pressable
              accessibilityLabel={`Select delivery address ${address.addressKey}`}
              accessibilityRole="button"
              key={address.addressVersionId}
              onPress={() => setSelectedAddressId(address.addressVersionId)}
              style={[
                styles.customer,
                selectedAddressId === address.addressVersionId &&
                  styles.customerSelected,
              ]}
            >
              <Text style={styles.customerCode}>{address.addressKey}</Text>
              <Text style={styles.copy}>
                {address.line1}, {address.city}
              </Text>
            </Pressable>
          ))}
          <Text style={styles.label}>PRICED ITEM / ENTERED UNIT</Text>
          {state.reference.items.map((item) => (
            <Pressable
              accessibilityLabel={`Select ${item.skuCode} in ${item.unitCode}`}
              accessibilityRole="button"
              key={item.priceListLineId}
              onPress={() => setSelectedItemId(item.priceListLineId)}
              style={[
                styles.customer,
                selectedItemId === item.priceListLineId &&
                  styles.customerSelected,
              ]}
            >
              <Text style={styles.customerCode}>
                {item.skuCode} / {item.unitCode}
              </Text>
              <Text style={styles.copy}>
                {state.reference?.currency} {item.listUnitPrice} ·{" "}
                {item.baseQuantityPerUnit} {item.baseStockingUnit}
              </Text>
            </Pressable>
          ))}
          {state.reference.items.find(
            (item) => item.priceListLineId === selectedItemId,
          ) !== undefined && (
            <>
              <Text style={styles.label}>
                {
                  state.reference.items.find(
                    (item) => item.priceListLineId === selectedItemId,
                  )!.skuCode
                }{" "}
                QUANTITY
              </Text>
              <TextInput
                accessibilityLabel="Sales Order quantity"
                inputMode="decimal"
                onChangeText={setQuantity}
                style={styles.input}
                value={quantity}
              />
              <Text style={styles.label}>ORDER DISCOUNT</Text>
              <TextInput
                accessibilityLabel="Order discount"
                inputMode="decimal"
                onChangeText={setDiscount}
                style={styles.input}
                value={discount}
              />
              <Text style={styles.label}>PAYMENT TIMING</Text>
              {(["prepaid", "cash_on_delivery", "on_account"] as const).map(
                (policy) => (
                  <Pressable
                    accessibilityLabel={`Use ${policy.replaceAll("_", " ")}`}
                    accessibilityRole="button"
                    key={policy}
                    onPress={() => setPaymentPolicy(policy)}
                    style={[
                      styles.customer,
                      paymentPolicy === policy && styles.customerSelected,
                    ]}
                  >
                    <Text style={styles.customerCode}>
                      {policy.replaceAll("_", " ").toUpperCase()}
                    </Text>
                  </Pressable>
                ),
              )}
              {paymentPolicy !== state.reference.paymentTimingDefault && (
                <TextInput
                  accessibilityLabel="Payment timing override reason"
                  onChangeText={setPaymentOverrideReason}
                  placeholder="Required override reason"
                  style={styles.input}
                  value={paymentOverrideReason}
                />
              )}
              <Text style={styles.label}>AUTHORIZED PRICE OVERRIDE</Text>
              <TextInput
                accessibilityLabel="Manual unit price"
                inputMode="decimal"
                onChangeText={setOverridePrice}
                placeholder="Leave blank for list price"
                style={styles.input}
                value={overridePrice}
              />
              <TextInput
                accessibilityLabel="Price override reason"
                onChangeText={setPriceOverrideReason}
                placeholder="Required with override"
                style={styles.input}
                value={priceOverrideReason}
              />
              {validationMessage !== null && (
                <Text accessibilityLiveRegion="assertive" style={styles.error}>
                  {validationMessage}
                </Text>
              )}
              <Pressable
                accessibilityRole="button"
                disabled={Number(quantity) <= 0}
                onPress={save}
                style={styles.button}
              >
                <Text style={styles.buttonText}>SAVE OFFLINE DRAFT</Text>
              </Pressable>
            </>
          )}
        </View>
      )}
      {localDraft !== null && (
        <View style={styles.status}>
          <Text
            accessibilityLiveRegion="polite"
            accessibilityRole="header"
            style={styles.panelTitle}
          >
            {localDraft.status === "synced"
              ? "Draft acknowledged by TradeFlow"
              : localDraft.status === "conflict"
                ? "Sync conflict — review required"
                : "Pending Sync"}
          </Text>
          <Text style={styles.copy}>
            {localDraft.status === "synced"
              ? `Server revision ${localDraft.savedDraft?.version ?? 1}`
              : "The command and idempotency key are stored durably on this device."}
          </Text>
          {localDraft.status === "conflict" &&
            localDraft.savedDraft !== null && (
              <Text style={styles.reference}>
                SERVER V{localDraft.savedDraft.version} /{" "}
                {localDraft.savedDraft.currency}{" "}
                {localDraft.savedDraft.grandTotal} · LOCAL QUANTITY{" "}
                {localDraft.command.lines[0]?.quantity ?? "—"}
              </Text>
            )}
          {localDraft.status !== "synced" && (
            <Pressable
              accessibilityRole="button"
              disabled={syncing || localDraft.status === "conflict"}
              onPress={synchronize}
              style={styles.button}
            >
              <Text style={styles.buttonText}>
                {syncing ? "SYNCING…" : "RETRY SYNC"}
              </Text>
            </Pressable>
          )}
          {localDraft.status === "conflict" &&
            localDraft.savedDraft !== null && (
              <Pressable
                accessibilityRole="button"
                onPress={resolveConflict}
                style={styles.button}
              >
                <Text style={styles.buttonText}>
                  USE SERVER REVISION, KEEP LOCAL EDITS
                </Text>
              </Pressable>
            )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  button: {
    alignItems: "center",
    backgroundColor: colors.ink,
    marginTop: 16,
    padding: 15,
  },
  buttonText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 12,
    letterSpacing: 1,
  },
  copy: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 15,
    lineHeight: 23,
  },
  customer: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    paddingVertical: 14,
  },
  customerCode: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 14,
  },
  customerSelected: {
    borderLeftColor: colors.orange,
    borderLeftWidth: 4,
    paddingLeft: 12,
  },
  eyebrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1.5,
  },
  error: {
    color: colors.red,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 14,
    marginTop: 12,
  },
  input: {
    borderColor: colors.ink,
    borderWidth: 1,
    color: colors.ink,
    fontFamily: "IBMPlexSans_500Medium",
    fontSize: 18,
    marginTop: 8,
    padding: 14,
  },
  label: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1,
    marginTop: 22,
  },
  panel: { marginTop: 28 },
  panelTitle: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 26,
    marginVertical: 10,
  },
  pending: {
    alignSelf: "flex-start",
    backgroundColor: colors.orange,
    color: colors.ink,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    padding: 8,
  },
  reference: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_500Medium",
    fontSize: 12,
    marginTop: 12,
  },
  screen: {
    backgroundColor: colors.paper,
    flexGrow: 1,
    padding: 24,
    paddingTop: 64,
  },
  status: {
    borderTopColor: colors.ink,
    borderTopWidth: 2,
    marginTop: 28,
    paddingTop: 18,
  },
  title: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 43,
    lineHeight: 45,
    marginVertical: 12,
  },
});
