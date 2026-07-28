import {
  searchInventoryDirectory,
  type InventoryDirectoryState,
  type SearchInventoryDirectoryOptions,
} from "@tradeflow/inventory-directory";
import { colors } from "@tradeflow/design-tokens";
import { randomUUID } from "expo-crypto";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

export type InventoryDirectoryProps = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId?: () => string;
  fetch?: SearchInventoryDirectoryOptions["fetch"];
};

type ScreenState = InventoryDirectoryState | { kind: "loading" };

export function InventoryDirectory({
  accessToken,
  baseUrl,
  createCorrelationId = randomUUID,
  fetch,
}: InventoryDirectoryProps) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<ScreenState>({ kind: "loading" });
  const search = useCallback(
    async (nextQuery: string) => {
      setState({ kind: "loading" });
      try {
        setState(
          await searchInventoryDirectory({
            accessToken,
            baseUrl,
            correlationId: createCorrelationId(),
            ...(fetch === undefined ? {} : { fetch }),
            query: nextQuery,
          }),
        );
      } catch {
        setState({ correlationId: createCorrelationId(), kind: "unavailable" });
      }
    },
    [accessToken, baseUrl, createCorrelationId, fetch],
  );

  useEffect(() => {
    let active = true;
    void searchInventoryDirectory({
      accessToken,
      baseUrl,
      correlationId: createCorrelationId(),
      ...(fetch === undefined ? {} : { fetch }),
      query: "",
    })
      .then((next) => {
        if (active) setState(next);
      })
      .catch(() => {
        if (active) {
          setState({
            correlationId: createCorrelationId(),
            kind: "unavailable",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [accessToken, baseUrl, createCorrelationId, fetch]);

  return (
    <ScrollView
      contentContainerStyle={styles.screen}
      contentInsetAdjustmentBehavior="automatic"
      keyboardShouldPersistTaps="handled"
    >
      <View style={styles.header}>
        <View style={styles.brandMark}>
          <Text style={styles.brandText}>TF</Text>
        </View>
        <Text style={styles.headerTitle}>INVENTORY LOOKUP</Text>
        <Text style={styles.headerMeta}>FIELD / 04</Text>
      </View>
      <View style={styles.intro}>
        <Text style={styles.eyebrow}>WAREHOUSE AVAILABILITY</Text>
        <Text accessibilityRole="header" style={styles.display}>
          Check stock before you promise it.
        </Text>
        <Text style={styles.copy}>
          Server-committed quantities from your assigned Warehouses only.
        </Text>
      </View>
      <View style={styles.search}>
        <Text style={styles.label}>SKU CODE OR PRODUCT NAME</Text>
        <View style={styles.searchRow}>
          <TextInput
            accessibilityLabel="Inventory search"
            onChangeText={setQuery}
            onSubmitEditing={() => void search(query)}
            placeholder="COLA-330"
            placeholderTextColor={colors.inkMuted}
            returnKeyType="search"
            style={styles.input}
            value={query}
          />
          <Pressable
            accessibilityLabel="Search inventory"
            accessibilityRole="button"
            onPress={() => void search(query)}
            style={styles.searchButton}
          >
            <Text style={styles.searchButtonText}>SEARCH</Text>
          </Pressable>
        </View>
      </View>
      <DirectoryState retry={() => void search(query)} state={state} />
    </ScrollView>
  );
}

function DirectoryState({
  retry,
  state,
}: {
  retry: () => void;
  state: ScreenState;
}) {
  if (state.kind === "loading") {
    return (
      <View accessibilityRole="progressbar" style={styles.message}>
        <ActivityIndicator color={colors.orange} />
        <Text style={styles.messageTitle}>Loading scoped stock…</Text>
      </View>
    );
  }
  if (state.kind !== "ready") {
    const title =
      state.kind === "forbidden"
        ? "Inventory access is not assigned"
        : state.kind === "unauthenticated"
          ? "Sign in to view stock"
          : state.kind === "validation"
            ? "Revise the search"
            : "Inventory temporarily unavailable";
    return (
      <View style={styles.message}>
        <Text style={styles.alert}>!</Text>
        <Text accessibilityRole="header" style={styles.messageTitle}>
          {title}
        </Text>
        <Text style={styles.copy}>
          {state.kind === "forbidden"
            ? "Ask for inventory read access and Warehouse scope."
            : state.kind === "unauthenticated"
              ? "Sign in, then return to Inventory Lookup."
              : state.kind === "validation"
                ? "Enter a valid SKU code or product name."
                : "Confirm your connection and try again."}
        </Text>
        <Text selectable style={styles.reference}>
          Support reference {state.correlationId}
        </Text>
        {state.kind === "unavailable" && (
          <Pressable
            accessibilityLabel="Retry inventory search"
            accessibilityRole="button"
            onPress={retry}
            style={styles.retry}
          >
            <Text style={styles.retryText}>RETRY AVAILABILITY →</Text>
          </Pressable>
        )}
      </View>
    );
  }
  if (state.total === 0) {
    return (
      <View style={styles.message}>
        <Text style={styles.empty}>∅</Text>
        <Text accessibilityRole="header" style={styles.messageTitle}>
          No stock in your Warehouse scope
        </Text>
        <Text style={styles.copy}>
          Revise the search or contact inventory control.
        </Text>
      </View>
    );
  }
  return (
    <View style={styles.results}>
      <Text style={styles.resultCount}>
        {state.total.toString().padStart(2, "0")} STOCK POSITIONS
      </Text>
      {state.items.map((item) => (
        <View
          key={`${item.skuId}:${item.warehouseId}:${item.locationCode}:${item.lotCode ?? item.serialNumbers.join(",")}`}
          style={styles.card}
        >
          <View style={styles.cardTop}>
            <Text style={styles.skuCode}>{item.skuCode}</Text>
            <Text style={styles.custody}>{item.custody.toUpperCase()}</Text>
          </View>
          <Text style={styles.skuName}>{item.skuName}</Text>
          <View style={styles.quantities}>
            <View>
              <Text style={styles.metricLabel}>ON HAND</Text>
              <Text style={styles.metric}>{item.onHand}</Text>
            </View>
            <View>
              <Text style={styles.metricLabel}>RESERVED</Text>
              <Text style={styles.metric}>{item.reserved}</Text>
            </View>
            <View>
              <Text style={styles.metricLabel}>AVAILABLE</Text>
              <Text style={[styles.metric, styles.available]}>
                {item.available}
              </Text>
            </View>
          </View>
          <Text style={styles.trace}>
            {item.warehouseCode} / {item.locationCode} · {item.baseStockingUnit}
          </Text>
          <Text style={styles.trace}>
            {item.trackingPolicy.toUpperCase()} ·{" "}
            {item.lotCode ??
              (item.serialNumbers.length > 0
                ? item.serialNumbers.join(", ")
                : "NO IDENTITY")}{" "}
            · {item.expirationDate ?? "NO EXPIRATION"}
          </Text>
          <Text style={styles.trace}>
            MOVING AVG {item.baseCurrency} {item.movingAverageUnitCost} ·
            WAREHOUSE VALUE {item.baseCurrency} {item.warehouseInventoryValue}
          </Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  alert: {
    color: colors.red,
    fontFamily: "Newsreader_500Medium",
    fontSize: 54,
  },
  available: { color: colors.orange },
  brandMark: {
    alignItems: "center",
    backgroundColor: colors.ink,
    height: 38,
    justifyContent: "center",
    width: 38,
  },
  brandText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
  },
  card: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    paddingVertical: 20,
  },
  cardTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  copy: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 15,
    lineHeight: 23,
  },
  custody: {
    borderColor: colors.orange,
    borderWidth: 1,
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 9,
    paddingHorizontal: 6,
    paddingVertical: 3,
  },
  display: {
    color: colors.ink,
    fontFamily: "Newsreader_500Medium",
    fontSize: 48,
    letterSpacing: -2.4,
    lineHeight: 47,
  },
  empty: {
    color: colors.orange,
    fontFamily: "Newsreader_500Medium",
    fontSize: 54,
  },
  eyebrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1.5,
  },
  header: {
    alignItems: "center",
    borderBottomColor: colors.ink,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: 12,
    paddingBottom: 16,
  },
  headerMeta: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 10,
    marginLeft: "auto",
  },
  headerTitle: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1,
  },
  input: {
    backgroundColor: colors.paper,
    borderColor: colors.inkMuted,
    borderWidth: 1,
    color: colors.ink,
    flex: 1,
    fontFamily: "IBMPlexSans_400Regular",
    minHeight: 48,
    paddingHorizontal: 12,
  },
  intro: { gap: 10, paddingVertical: 42 },
  label: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1,
    marginBottom: 7,
  },
  message: {
    alignItems: "flex-start",
    borderTopColor: colors.ink,
    borderTopWidth: 2,
    gap: 12,
    minHeight: 280,
    paddingTop: 35,
  },
  messageTitle: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 29,
    lineHeight: 32,
  },
  metric: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 24,
  },
  metricLabel: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 8,
    letterSpacing: 1,
  },
  quantities: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginVertical: 14,
    paddingVertical: 12,
  },
  reference: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 11,
  },
  resultCount: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1.2,
    paddingVertical: 12,
  },
  results: { borderTopColor: colors.ink, borderTopWidth: 2 },
  retry: {
    backgroundColor: colors.ink,
    marginTop: 12,
    minHeight: 44,
    paddingHorizontal: 18,
    paddingVertical: 14,
  },
  retryText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1,
  },
  screen: {
    backgroundColor: colors.paper,
    flexGrow: 1,
    paddingBottom: 48,
    paddingHorizontal: 20,
    paddingTop: 16,
  },
  search: {
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    paddingVertical: 16,
  },
  searchButton: {
    alignItems: "center",
    backgroundColor: colors.ink,
    justifyContent: "center",
    minHeight: 48,
    paddingHorizontal: 16,
  },
  searchButtonText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1,
  },
  searchRow: { flexDirection: "row" },
  skuCode: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1,
  },
  skuName: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 28,
    marginTop: 8,
  },
  trace: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_500Medium",
    fontSize: 11,
    marginTop: 4,
  },
});
