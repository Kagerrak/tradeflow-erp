import {
  searchCustomerDirectory,
  type CustomerDirectoryState,
  type SearchCustomerDirectoryOptions,
} from "@tradeflow/customer-directory";
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

export type CustomerDirectoryProps = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId?: () => string;
  fetch?: SearchCustomerDirectoryOptions["fetch"];
};

type ScreenState = CustomerDirectoryState | { kind: "loading" };

const paymentLabels = {
  cash_on_delivery: "Cash on delivery",
  on_account: "On account",
  prepaid: "Prepaid",
} as const;

export function CustomerDirectory({
  accessToken,
  baseUrl,
  createCorrelationId = randomUUID,
  fetch,
}: CustomerDirectoryProps) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<ScreenState>({ kind: "loading" });

  const search = useCallback(
    async (nextQuery: string) => {
      setState({ kind: "loading" });
      try {
        setState(
          await searchCustomerDirectory({
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
    void searchCustomerDirectory({
      accessToken,
      baseUrl,
      correlationId: createCorrelationId(),
      ...(fetch === undefined ? {} : { fetch }),
      query: "",
    })
      .then((nextState) => {
        if (active) setState(nextState);
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
        <Text style={styles.headerTitle}>CUSTOMER LOOKUP</Text>
        <Text style={styles.headerMeta}>FIELD / 03</Text>
      </View>
      <View style={styles.intro}>
        <Text style={styles.eyebrow}>AUTHORIZED DIRECTORY</Text>
        <Text accessibilityRole="header" style={styles.display}>
          Find the account before the stop.
        </Text>
        <Text style={styles.copy}>
          Results are limited to your assigned operational Branches.
        </Text>
      </View>
      <View style={styles.search}>
        <Text style={styles.label}>ACCOUNT NUMBER OR LEGAL NAME</Text>
        <View style={styles.searchRow}>
          <TextInput
            accessibilityLabel="Customer search"
            onChangeText={setQuery}
            onSubmitEditing={() => void search(query)}
            placeholder="MNL-0042 or Northstar"
            placeholderTextColor={colors.inkMuted}
            returnKeyType="search"
            style={styles.input}
            value={query}
          />
          <Pressable
            accessibilityLabel="Search customers"
            accessibilityRole="button"
            onPress={() => void search(query)}
            style={styles.searchButton}
          >
            <Text style={styles.searchButtonText}>SEARCH</Text>
          </Pressable>
        </View>
      </View>
      <DirectoryState state={state} retry={() => void search(query)} />
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
      <View
        accessibilityLabel="Loading scoped customer accounts"
        accessibilityRole="progressbar"
        style={styles.message}
      >
        <ActivityIndicator color={colors.orange} />
        <Text style={styles.messageTitle}>Loading scoped accounts…</Text>
      </View>
    );
  }
  if (state.kind !== "ready") {
    const title =
      state.kind === "forbidden"
        ? "Customer access is not assigned"
        : state.kind === "unauthenticated"
          ? "Sign in to continue"
          : state.kind === "validation"
            ? "Use at least two search characters"
            : "Directory temporarily unavailable";
    return (
      <View style={styles.message}>
        <Text style={styles.alertMark}>!</Text>
        <Text accessibilityRole="header" style={styles.messageTitle}>
          {title}
        </Text>
        <Text style={styles.copy}>
          {state.kind === "forbidden"
            ? "Ask an operations administrator for customer read access and a Branch assignment."
            : state.kind === "unauthenticated"
              ? "Open your identity provider, then return to customer lookup."
              : state.kind === "validation"
                ? "Enter a longer account number or legal name, then search again."
                : "Confirm your connection and try again."}
        </Text>
        <Text selectable style={styles.supportReference}>
          Support reference {state.correlationId}
        </Text>
        {state.kind === "unavailable" && (
          <Pressable
            accessibilityLabel="Retry customer search"
            accessibilityRole="button"
            onPress={retry}
            style={styles.retry}
          >
            <Text style={styles.retryText}>RETRY SEARCH →</Text>
          </Pressable>
        )}
      </View>
    );
  }
  if (state.total === 0) {
    return (
      <View style={styles.message}>
        <Text style={styles.emptyMark}>∅</Text>
        <Text accessibilityRole="header" style={styles.messageTitle}>
          No accounts in your scope
        </Text>
        <Text style={styles.copy}>
          Revise the search or ask the desk to onboard the account.
        </Text>
      </View>
    );
  }
  return (
    <View style={styles.results}>
      <Text style={styles.resultCount}>
        {state.total.toString().padStart(2, "0")} ACCOUNTS
      </Text>
      {state.items.map((customer) => (
        <View key={customer.customerId} style={styles.account}>
          <View style={styles.accountTop}>
            <Text style={styles.accountNumber}>{customer.accountNumber}</Text>
            <Text style={styles.status}>{customer.status.toUpperCase()}</Text>
          </View>
          <Text style={styles.accountName}>{customer.legalName}</Text>
          <View style={styles.accountTerms}>
            <Text style={styles.term}>
              {paymentLabels[customer.paymentTimingPolicy]}
            </Text>
            <Text style={[styles.term, customer.creditHold && styles.hold]}>
              {customer.creditHold ? "CREDIT HOLD" : "CREDIT CLEAR"}
            </Text>
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  account: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    paddingVertical: 18,
  },
  accountName: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 27,
    marginVertical: 8,
  },
  accountNumber: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 12,
    letterSpacing: 1,
  },
  accountTerms: { flexDirection: "row", gap: 8 },
  accountTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  alertMark: {
    color: colors.red,
    fontFamily: "Newsreader_500Medium",
    fontSize: 54,
  },
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
  copy: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 15,
    lineHeight: 23,
    maxWidth: 440,
  },
  display: {
    color: colors.ink,
    fontFamily: "Newsreader_500Medium",
    fontSize: 49,
    letterSpacing: -2.5,
    lineHeight: 47,
    maxWidth: 390,
  },
  emptyMark: {
    color: colors.orange,
    fontFamily: "Newsreader_500Medium",
    fontSize: 54,
  },
  eyebrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1.5,
    marginBottom: 10,
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
  hold: { color: colors.red },
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
  intro: { gap: 8, paddingVertical: 42 },
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
    paddingHorizontal: 16,
  },
  searchButtonText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1,
  },
  searchRow: { flexDirection: "row" },
  status: {
    borderColor: colors.green,
    borderWidth: 1,
    color: colors.green,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 9,
    paddingHorizontal: 5,
    paddingVertical: 2,
  },
  supportReference: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 11,
  },
  term: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 10,
    letterSpacing: 0.5,
  },
});
