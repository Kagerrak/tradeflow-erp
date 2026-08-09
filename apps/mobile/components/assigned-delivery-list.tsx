import {
  listAssignedDeliveries,
  type AssignedDelivery,
} from "@tradeflow/delivery-dispatch";
import { colors } from "@tradeflow/design-tokens";
import { randomUUID } from "expo-crypto";
import { getNetworkStateAsync } from "expo-network";
import { useCallback, useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type { AssignedDeliveryCache } from "../offline/assigned-delivery-cache";

type ViewState =
  | { kind: "loading" }
  | { deliveries: AssignedDelivery[]; kind: "live" }
  | { deliveries: AssignedDelivery[]; kind: "cached"; savedAt: string }
  | { code: string; kind: "forbidden" }
  | { kind: "empty" }
  | { kind: "unavailable" };

export type AssignedDeliveryListProps = {
  accessToken: string | undefined;
  baseUrl: string;
  cache: AssignedDeliveryCache;
  createId?: () => string;
  fetch?: (request: Request) => Promise<Response>;
  isOnline?: () => Promise<boolean>;
  onConfirm?: (delivery: AssignedDelivery) => void;
  subject: string;
};

export function AssignedDeliveryList({
  accessToken,
  baseUrl,
  cache,
  createId = randomUUID,
  fetch,
  isOnline = async () => {
    const network = await getNetworkStateAsync();
    return (
      network.isConnected === true && network.isInternetReachable !== false
    );
  },
  onConfirm,
  subject,
}: AssignedDeliveryListProps) {
  const [state, setState] = useState<ViewState>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    if (!(await isOnline())) {
      const cached = await cache.load(subject);
      setState(
        cached === null
          ? { kind: "unavailable" }
          : {
              deliveries: cached.deliveries,
              kind: "cached",
              savedAt: cached.savedAt,
            },
      );
      return;
    }
    const result = await listAssignedDeliveries({
      accessToken,
      baseUrl,
      correlationId: createId(),
      ...(fetch === undefined ? {} : { fetch }),
    });
    if (result.kind === "ready") {
      const savedAt = new Date().toISOString();
      await cache.replace({
        cacheTag: result.cacheTag,
        deliveries: result.items,
        savedAt,
        subject,
      });
      setState(
        result.items.length === 0
          ? { kind: "empty" }
          : { deliveries: result.items, kind: "live" },
      );
      return;
    }
    if (result.kind === "forbidden" || result.kind === "unauthenticated") {
      await cache.remove(subject);
      setState({ code: result.code, kind: "forbidden" });
      return;
    }
    const cached = await cache.load(subject);
    setState(
      cached === null
        ? { kind: "unavailable" }
        : {
            deliveries: cached.deliveries,
            kind: "cached",
            savedAt: cached.savedAt,
          },
    );
  }, [accessToken, baseUrl, cache, createId, fetch, isOnline, subject]);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      if (active) void load();
    }, 0);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [load]);

  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Text style={styles.eyebrow}>ASSIGNED RUN / 006</Text>
      <Text accessibilityRole="header" style={styles.title}>
        Carry only the work assigned to you.
      </Text>
      <Text style={styles.intro}>
        Recipient, address, identities, payment context, and required evidence
        travel together as one scoped Delivery task.
      </Text>
      {state.kind === "loading" && <State title="Refreshing assignment" />}
      {state.kind === "empty" && <State title="No assigned Deliveries" />}
      {state.kind === "unavailable" && (
        <State title="No authorized cache is available" />
      )}
      {state.kind === "forbidden" && (
        <State title="Delivery access revoked" detail={state.code} critical />
      )}
      {(state.kind === "live" || state.kind === "cached") && (
        <>
          {state.kind === "cached" && (
            <View style={styles.cacheNotice}>
              <Text style={styles.cacheKicker}>OFFLINE CACHE</Text>
              <Text accessibilityRole="header" style={styles.cacheTitle}>
                Cached task — authorization refresh required
              </Text>
              <Text style={styles.cacheText}>
                Authorized snapshot from {state.savedAt}. Proof can be captured
                offline; posting waits for the server to recheck this
                assignment.
              </Text>
            </View>
          )}
          {state.deliveries.map((delivery) => (
            <DeliveryCard
              canConfirm
              delivery={delivery}
              key={delivery.deliveryId}
              {...(onConfirm === undefined ? {} : { onConfirm })}
            />
          ))}
        </>
      )}
      <Pressable
        accessibilityRole="button"
        onPress={() => void load()}
        style={styles.refresh}
      >
        <Text style={styles.refreshText}>REFRESH AUTHORIZED RUNS</Text>
      </Pressable>
    </ScrollView>
  );
}

function State({
  critical = false,
  detail,
  title,
}: {
  critical?: boolean;
  detail?: string;
  title: string;
}) {
  return (
    <View style={[styles.state, critical && styles.stateCritical]}>
      <Text accessibilityRole="header" style={styles.stateTitle}>
        {title}
      </Text>
      {detail !== undefined && <Text style={styles.stateDetail}>{detail}</Text>}
    </View>
  );
}

function DeliveryCard({
  canConfirm,
  delivery,
  onConfirm,
}: {
  canConfirm: boolean;
  delivery: AssignedDelivery;
  onConfirm?: (delivery: AssignedDelivery) => void;
}) {
  const address = delivery.deliveryAddress;
  const addressLine = [
    address["line_1"],
    address["city"],
    address["region"],
    address["postal_code"],
  ]
    .filter((value): value is string => typeof value === "string")
    .join(", ");
  return (
    <View style={styles.card}>
      <Text style={styles.manifest}>DELIVERY {delivery.deliveryId}</Text>
      <Text accessibilityRole="header" style={styles.recipient}>
        {delivery.recipientName}
      </Text>
      <Text style={styles.address}>{addressLine}</Text>
      <View style={styles.paymentRail}>
        <Text style={styles.paymentText}>
          {delivery.paymentTimingPolicy === "cash_on_delivery"
            ? `CASH ON DELIVERY · PHP ${delivery.collectionAmountDue ?? "DUE UNAVAILABLE"} · COLLECTION REQUIRED`
            : delivery.paymentTimingPolicy.replaceAll("_", " ").toUpperCase()}
        </Text>
      </View>
      {delivery.lines.map((line) => (
        <View key={line.lineId} style={styles.line}>
          <Text style={styles.lineName}>{line.skuName}</Text>
          <Text style={styles.quantity}>{line.quantityBase}</Text>
          {line.serialNumbers.map((serial) => (
            <Text key={serial} style={styles.identity}>
              SERIAL {serial}
            </Text>
          ))}
          {line.lotSelections.map((lot) => (
            <Text key={lot.lotCode} style={styles.identity}>
              LOT {lot.lotCode} · {lot.quantityBase}
            </Text>
          ))}
        </View>
      ))}
      <Text style={styles.evidence}>
        Evidence: {delivery.evidenceRequirements.join(" · ")}
      </Text>
      {delivery.status === "dispatched" && onConfirm !== undefined && (
        <Pressable
          accessibilityRole="button"
          disabled={!canConfirm}
          onPress={() => onConfirm(delivery)}
          style={styles.confirm}
        >
          <Text style={styles.confirmText}>
            {canConfirm
              ? "CAPTURE ACCEPTANCE"
              : "REFRESH AUTHORIZATION TO CONFIRM"}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  address: { color: colors.inkMuted, lineHeight: 22, marginBottom: 16 },
  cacheKicker: {
    color: colors.orange,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  cacheNotice: {
    borderTopColor: colors.orange,
    borderTopWidth: 1,
    marginBottom: 20,
    paddingTop: 16,
  },
  cacheText: { color: colors.inkMuted, lineHeight: 21 },
  cacheTitle: {
    color: colors.ink,
    fontSize: 20,
    fontWeight: "700",
    marginBottom: 8,
    marginTop: 6,
  },
  card: {
    borderTopColor: colors.ink,
    borderTopWidth: 1,
    marginBottom: 28,
    paddingTop: 16,
  },
  confirm: { backgroundColor: colors.ink, marginTop: 16, padding: 14 },
  confirmText: {
    color: colors.paper,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1,
  },
  evidence: {
    color: colors.inkMuted,
    fontSize: 12,
    marginTop: 14,
    textTransform: "uppercase",
  },
  eyebrow: {
    color: colors.orange,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.6,
  },
  identity: {
    color: colors.inkMuted,
    fontFamily: "monospace",
    fontSize: 12,
    marginTop: 4,
  },
  intro: {
    color: colors.inkMuted,
    fontSize: 16,
    lineHeight: 24,
    marginBottom: 28,
  },
  line: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    paddingBottom: 14,
    paddingTop: 14,
  },
  lineName: { color: colors.ink, fontSize: 16, fontWeight: "700" },
  manifest: {
    color: colors.inkMuted,
    fontFamily: "monospace",
    fontSize: 11,
    marginBottom: 10,
  },
  paymentRail: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    paddingVertical: 12,
  },
  paymentText: {
    color: colors.orange,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 0.8,
  },
  quantity: { color: colors.ink, fontFamily: "monospace", marginTop: 6 },
  recipient: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 30,
    marginBottom: 8,
  },
  refresh: {
    alignItems: "center",
    backgroundColor: colors.ink,
    minHeight: 50,
    justifyContent: "center",
    marginTop: 8,
  },
  refreshText: {
    color: colors.paper,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1,
  },
  screen: {
    backgroundColor: colors.paper,
    flexGrow: 1,
    padding: 24,
    paddingBottom: 48,
    paddingTop: 48,
  },
  state: {
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    marginBottom: 24,
    minHeight: 180,
    paddingTop: 18,
  },
  stateCritical: { borderTopColor: colors.red },
  stateDetail: { color: colors.red, fontFamily: "monospace", marginTop: 8 },
  stateTitle: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 28,
  },
  title: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 42,
    letterSpacing: -1.5,
    lineHeight: 44,
    marginBottom: 14,
    marginTop: 8,
  },
});
