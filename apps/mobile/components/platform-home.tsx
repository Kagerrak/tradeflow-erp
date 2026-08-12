import {
  loadPlatformSession,
  platformStateContent,
  type LoadPlatformSessionOptions,
  type PlatformSessionState,
} from "@tradeflow/platform-session";
import { createTelemetryContext } from "@tradeflow/telemetry";
import { colors } from "@tradeflow/design-tokens";
import { randomUUID } from "expo-crypto";
import { Link } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

export type PlatformHomeProps = {
  accessToken: string | undefined;
  baseUrl: string;
  createCorrelationId?: () => string;
  fetch?: LoadPlatformSessionOptions["fetch"];
};

type HomeState = PlatformSessionState | { kind: "loading" };

const routeTasks = [
  ["01", "Customer lookup", "Account and delivery context"],
  ["02", "Sales order draft", "Priced capture with durable Pending Sync"],
  ["03", "Payment receipt", "Capture evidence; server controls clearance"],
  ["04", "Pick list", "Reserved goods and tracked identities"],
] as const;

export function PlatformHome({
  accessToken,
  baseUrl,
  createCorrelationId = randomUUID,
  fetch,
}: PlatformHomeProps) {
  const [state, setState] = useState<HomeState>({ kind: "loading" });
  const requestSession =
    useCallback(async (): Promise<PlatformSessionState> => {
      const { correlationId } = createTelemetryContext(
        "tradeflow-mobile",
        createCorrelationId,
      );
      try {
        return await loadPlatformSession({
          accessToken,
          baseUrl,
          correlationId,
          ...(fetch === undefined ? {} : { fetch }),
        });
      } catch {
        return {
          correlationId,
          kind: "unavailable",
        };
      }
    }, [accessToken, baseUrl, createCorrelationId, fetch]);

  useEffect(() => {
    let active = true;
    void requestSession().then((nextState) => {
      if (active) {
        setState(nextState);
      }
    });

    return () => {
      active = false;
    };
  }, [requestSession]);

  const retry = async () => {
    setState({ kind: "loading" });
    setState(await requestSession());
  };

  return (
    <ScrollView
      contentContainerStyle={styles.screen}
      contentInsetAdjustmentBehavior="automatic"
    >
      <FieldHeader />
      <View style={styles.intro}>
        <Text style={styles.eyebrow}>FIELD CONTROL / 001</Text>
        <Text accessibilityRole="header" style={styles.display}>
          Ready for the route.
        </Text>
        <Text style={styles.introCopy}>
          Confirm authority and connectivity before capturing work away from the
          desk.
        </Text>
      </View>
      <View style={styles.rule} />
      <StatePanel retry={retry} state={state} />
      <RoutePreview />
      <View style={styles.footer}>
        <Text style={styles.footerText}>TRADEFLOW ERP</Text>
        <View style={styles.footerRule} />
        <Text style={styles.footerText}>MOBILE / V0.1</Text>
      </View>
    </ScrollView>
  );
}

function FieldHeader() {
  return (
    <View style={styles.header}>
      <View style={styles.brandMark}>
        <View style={styles.brandSlash} />
        <Text style={styles.brandInitials}>TF</Text>
        <View style={styles.brandDot} />
      </View>
      <View style={styles.brandText}>
        <Text style={styles.brandName}>TradeFlow</Text>
        <Text style={styles.brandCaption}>DISTRIBUTION FIELD OPS</Text>
      </View>
      <View style={styles.environment}>
        <View style={styles.environmentDot} />
        <Text style={styles.environmentText}>DEV</Text>
      </View>
    </View>
  );
}

function StatePanel({
  retry,
  state,
}: {
  retry: () => Promise<void>;
  state: HomeState;
}) {
  if (state.kind === "loading") {
    return (
      <View
        accessibilityLabel="Checking identity, API, and database"
        accessibilityRole="progressbar"
        style={styles.statePanel}
      >
        <Text style={styles.stateIndex}>CHECK / 03</Text>
        <ActivityIndicator
          accessibilityElementsHidden
          color={colors.orange}
          size="small"
          style={styles.spinner}
        />
        <Text style={styles.stateKicker}>ESTABLISHING AUTHORITY</Text>
        <Text accessibilityRole="header" style={styles.stateHeading}>
          Checking identity, API, and database…
        </Text>
        <Text style={styles.bodyCopy}>
          Field work opens only after TradeFlow confirms the server session.
        </Text>
      </View>
    );
  }

  if (state.kind === "ready") {
    return (
      <View style={styles.statePanel}>
        <Text style={styles.stateIndex}>READY / 03</Text>
        <View style={styles.headingRow}>
          <View style={[styles.seal, styles.sealReady]}>
            <Text style={styles.sealReadyText}>✓</Text>
          </View>
          <View style={styles.headingCopy}>
            <Text style={styles.stateKicker}>SERVER ACKNOWLEDGED</Text>
            <Text accessibilityRole="header" style={styles.stateHeading}>
              Field handoff is ready
            </Text>
          </View>
        </View>
        <Text style={styles.bodyCopy}>
          Identity, service, and primary data agree. Nothing captured offline
          will appear posted until the server accepts it.
        </Text>

        <View style={styles.checklist}>
          <CheckRow label="Identity" value={state.user.displayName} />
          <CheckRow label="API" value={state.service} />
          <CheckRow label="Database" value={state.database} />
        </View>

        <View style={styles.correlation}>
          <Text style={styles.metaLabel}>CORRELATION</Text>
          <Text selectable style={styles.correlationValue}>
            {state.correlationId}
          </Text>
        </View>

        <View style={styles.nextAction}>
          <Text style={styles.nextNumber}>NEXT / 01</Text>
          <View style={styles.nextCopy}>
            <Text style={styles.nextTitle}>Configure field scope</Text>
            <Text style={styles.nextBody}>
              Branch, warehouse, and assignments begin in issue #3.
            </Text>
          </View>
          <Text style={styles.nextArrow}>→</Text>
        </View>
      </View>
    );
  }

  const content = platformStateContent[state.kind];

  return (
    <View style={styles.statePanel}>
      <Text style={styles.stateIndex}>{content.index}</Text>
      <View style={styles.headingRow}>
        <View
          style={[
            styles.seal,
            content.tone === "error" ? styles.sealError : styles.sealWarning,
          ]}
        >
          <Text
            style={
              content.tone === "error"
                ? styles.sealErrorText
                : styles.sealWarningText
            }
          >
            !
          </Text>
        </View>
        <View style={styles.headingCopy}>
          <Text style={styles.stateKicker}>{content.kicker}</Text>
          <Text accessibilityRole="header" style={styles.stateHeading}>
            {content.heading}
          </Text>
        </View>
      </View>
      <Text style={styles.bodyCopy}>{content.detail}</Text>
      <View style={styles.recovery}>
        <Text style={styles.metaLabel}>RECOVERY</Text>
        <Text style={styles.recoveryText}>{content.action}</Text>
      </View>
      {state.kind === "unavailable" && (
        <Pressable
          accessibilityLabel="Retry connection"
          accessibilityRole="button"
          onPress={() => void retry()}
          style={({ pressed }) => [
            styles.retryButton,
            pressed && styles.retryButtonPressed,
          ]}
        >
          <Text style={styles.retryText}>Retry connection</Text>
          <Text style={styles.retryArrow}>↗</Text>
        </Pressable>
      )}
      <Text selectable style={styles.supportReference}>
        Support reference {state.correlationId}
      </Text>
    </View>
  );
}

function CheckRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.checkRow}>
      <View style={styles.checkLabel}>
        <View style={styles.checkDot} />
        <Text style={styles.checkLabelText}>{label}</Text>
      </View>
      <Text style={styles.checkValue}>{value}</Text>
    </View>
  );
}

function RoutePreview() {
  return (
    <View style={styles.routePreview}>
      <View style={styles.routeHeading}>
        <View>
          <Text style={styles.eyebrow}>ASSIGNED WORK</Text>
          <Text accessibilityRole="header" style={styles.routeTitle}>
            One accountable route
          </Text>
        </View>
        <Text style={styles.routeCount}>4 TASKS</Text>
      </View>
      {routeTasks.map(([number, label, detail], index) =>
        index < 4 ? (
          <Link
            asChild
            href={
              index === 0
                ? "./customers"
                : index === 1
                  ? "./sales-orders"
                  : index === 2
                    ? "./payments"
                    : "./picking"
            }
            key={number}
          >
            <Pressable
              accessibilityLabel={`Open ${label.toLowerCase()}`}
              accessibilityRole="button"
              style={styles.routeRow}
            >
              <Text style={styles.routeNumber}>{number}</Text>
              <View style={styles.routeCopy}>
                <Text style={styles.routeLabel}>{label}</Text>
                <Text style={styles.routeDetail}>{detail}</Text>
              </View>
              <Text style={styles.locked}>OPEN →</Text>
            </Pressable>
          </Link>
        ) : (
          <View key={number} style={styles.routeRow}>
            <Text style={styles.routeNumber}>{number}</Text>
            <View style={styles.routeCopy}>
              <Text style={styles.routeLabel}>{label}</Text>
              <Text style={styles.routeDetail}>{detail}</Text>
            </View>
            <Text style={styles.locked}>LOCKED</Text>
          </View>
        ),
      )}
      <Text style={styles.routeNote}>
        Customer, Sales, Payment, and Pick capture respect live server scope.
        Offline Pick commands stay Pending Sync and never imply staged stock.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  bodyCopy: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 16,
    lineHeight: 25,
    marginBottom: 28,
  },
  brandCaption: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 9,
    letterSpacing: 1.2,
  },
  brandDot: {
    backgroundColor: colors.orange,
    bottom: 5,
    height: 5,
    position: "absolute",
    right: 5,
    width: 5,
  },
  brandInitials: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 0.5,
  },
  brandMark: {
    alignItems: "center",
    backgroundColor: colors.ink,
    height: 46,
    justifyContent: "center",
    overflow: "hidden",
    position: "relative",
    width: 46,
  },
  brandName: {
    color: colors.ink,
    fontFamily: "Newsreader_600SemiBold",
    fontSize: 21,
    lineHeight: 23,
  },
  brandSlash: {
    backgroundColor: colors.orange,
    height: 1,
    position: "absolute",
    transform: [{ rotate: "-42deg" }],
    width: 58,
  },
  brandText: {
    flex: 1,
    gap: 2,
  },
  checkDot: {
    backgroundColor: colors.green,
    borderRadius: 4,
    height: 8,
    width: 8,
  },
  checkLabel: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
  },
  checkLabelText: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 15,
  },
  checkRow: {
    alignItems: "center",
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 54,
  },
  checkValue: {
    color: colors.ink,
    flexShrink: 1,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 14,
    textAlign: "right",
  },
  checklist: {
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    marginBottom: 20,
  },
  correlation: {
    borderColor: colors.ink,
    borderWidth: 1,
    gap: 5,
    marginBottom: 20,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  correlationValue: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_500Medium",
    fontSize: 12,
    fontVariant: ["tabular-nums"],
  },
  display: {
    color: colors.ink,
    fontFamily: "Newsreader_500Medium",
    fontSize: 50,
    letterSpacing: -2.4,
    lineHeight: 48,
    maxWidth: 300,
  },
  environment: {
    alignItems: "center",
    borderColor: colors.paperDeep,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  environmentDot: {
    backgroundColor: colors.green,
    borderRadius: 3,
    height: 6,
    width: 6,
  },
  environmentText: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 10,
    letterSpacing: 0.8,
  },
  eyebrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 11,
    letterSpacing: 1.4,
    marginBottom: 8,
  },
  footer: {
    alignItems: "center",
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    flexDirection: "row",
    gap: 12,
    marginTop: 36,
    paddingTop: 14,
  },
  footerRule: {
    backgroundColor: colors.paperDeep,
    flex: 1,
    height: 1,
  },
  footerText: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 9,
    letterSpacing: 0.8,
  },
  header: {
    alignItems: "center",
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: 12,
    paddingBottom: 16,
  },
  headingCopy: {
    flex: 1,
  },
  headingRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 14,
    marginBottom: 20,
    marginTop: 8,
  },
  intro: {
    gap: 12,
    paddingBottom: 32,
    paddingTop: 44,
  },
  introCopy: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 16,
    lineHeight: 25,
    maxWidth: 340,
  },
  locked: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 9,
    letterSpacing: 0.9,
  },
  metaLabel: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 9,
    letterSpacing: 1.1,
  },
  nextAction: {
    alignItems: "center",
    backgroundColor: colors.ink,
    flexDirection: "row",
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 16,
  },
  nextArrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 24,
  },
  nextBody: {
    color: colors.paperDeep,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 12,
    lineHeight: 17,
  },
  nextCopy: {
    flex: 1,
    gap: 2,
  },
  nextNumber: {
    color: colors.amberSoft,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 9,
    letterSpacing: 0.8,
  },
  nextTitle: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 14,
  },
  recovery: {
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    gap: 5,
    marginBottom: 20,
    paddingVertical: 14,
  },
  recoveryText: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 15,
  },
  retryArrow: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_500Medium",
    fontSize: 20,
  },
  retryButton: {
    alignItems: "center",
    backgroundColor: colors.ink,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 52,
    paddingHorizontal: 16,
  },
  retryButtonPressed: {
    opacity: 0.8,
  },
  retryText: {
    color: colors.paper,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 15,
  },
  routeCopy: {
    flex: 1,
    gap: 2,
  },
  routeCount: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 9,
    letterSpacing: 0.8,
  },
  routeDetail: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 12,
    lineHeight: 17,
  },
  routeHeading: {
    alignItems: "flex-start",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  routeLabel: {
    color: colors.ink,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 15,
  },
  routeNote: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 13,
    lineHeight: 20,
    marginTop: 18,
  },
  routeNumber: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
  },
  routePreview: {
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    marginTop: 42,
    paddingTop: 26,
  },
  routeRow: {
    alignItems: "center",
    borderBottomColor: colors.paperDeep,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: 12,
    minHeight: 70,
  },
  routeTitle: {
    color: colors.ink,
    fontFamily: "Newsreader_500Medium",
    fontSize: 27,
    letterSpacing: -0.6,
  },
  rule: {
    backgroundColor: colors.ink,
    height: 1,
  },
  screen: {
    backgroundColor: colors.paper,
    flexGrow: 1,
    paddingBottom: 28,
    paddingHorizontal: 18,
    paddingTop: 14,
  },
  seal: {
    alignItems: "center",
    height: 44,
    justifyContent: "center",
    width: 44,
  },
  sealError: {
    backgroundColor: colors.redSoft,
    borderColor: colors.red,
    borderWidth: 1,
  },
  sealErrorText: {
    color: colors.red,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 18,
  },
  sealReady: {
    backgroundColor: colors.greenSoft,
    borderColor: colors.green,
    borderWidth: 1,
  },
  sealReadyText: {
    color: colors.green,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 18,
  },
  sealWarning: {
    backgroundColor: colors.amberSoft,
    borderColor: colors.amber,
    borderWidth: 1,
  },
  sealWarningText: {
    color: colors.amber,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 18,
  },
  spinner: {
    alignSelf: "flex-start",
    marginBottom: 26,
    marginTop: 14,
  },
  stateHeading: {
    color: colors.ink,
    fontFamily: "Newsreader_500Medium",
    fontSize: 35,
    letterSpacing: -1.2,
    lineHeight: 35,
  },
  stateIndex: {
    alignSelf: "flex-end",
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_600SemiBold",
    fontSize: 10,
    fontVariant: ["tabular-nums"],
    letterSpacing: 1,
  },
  stateKicker: {
    color: colors.orange,
    fontFamily: "IBMPlexSans_700Bold",
    fontSize: 10,
    letterSpacing: 1.2,
    marginBottom: 5,
  },
  statePanel: {
    minHeight: 470,
    paddingBottom: 8,
    paddingTop: 24,
  },
  supportReference: {
    color: colors.inkMuted,
    fontFamily: "IBMPlexSans_400Regular",
    fontSize: 10,
    fontVariant: ["tabular-nums"],
    lineHeight: 15,
    marginTop: 18,
  },
});
