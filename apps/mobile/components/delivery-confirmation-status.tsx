import { colors } from "@tradeflow/design-tokens";
import { useCallback, useEffect, useState } from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";

import type {
  DeliveryConfirmationStore,
  LocalDeliveryConfirmation,
} from "../offline/delivery-confirmation-store";

export function DeliveryConfirmationStatus({
  onRefreshReceipt,
  onReviewConflict,
  onSync,
  store,
}: {
  onRefreshReceipt?: (receiptId: string) => Promise<{
    accessUrl: string | null;
    number: string;
    status: "pending_document" | "ready" | "unavailable";
  }>;
  onReviewConflict?: (item: LocalDeliveryConfirmation) => Promise<void>;
  onSync: () => Promise<void>;
  store: DeliveryConfirmationStore;
}) {
  const [items, setItems] = useState<LocalDeliveryConfirmation[] | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [reviewErrors, setReviewErrors] = useState<Record<string, string>>({});
  const [receiptStates, setReceiptStates] = useState<
    Record<
      string,
      {
        accessUrl: string | null;
        number: string;
        status: "pending_document" | "ready" | "unavailable";
      }
    >
  >({});
  const refresh = useCallback(
    async () => setItems(await store.listCaptures()),
    [store],
  );
  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 0);
    return () => clearTimeout(timer);
  }, [refresh]);
  const sync = async () => {
    setSyncing(true);
    await onSync();
    await refresh();
    setSyncing(false);
  };
  return (
    <View style={styles.section}>
      <Text style={styles.eyebrow}>PROOF OF DELIVERY OUTBOX</Text>
      {items === null && (
        <Text accessibilityRole="header" style={styles.heading}>
          Loading captured confirmations
        </Text>
      )}
      {items?.length === 0 && (
        <Text accessibilityRole="header" style={styles.heading}>
          No captured confirmations
        </Text>
      )}
      {items?.map((item) => (
        <View key={item.confirmationId} style={styles.item}>
          <Text accessibilityRole="header" style={styles.heading}>
            {title(item)}
          </Text>
          <Text style={styles.detail}>Delivery {item.deliveryId}</Text>
          {(item.status === "conflict" ||
            item.status === "forbidden" ||
            item.authPaused) && (
            <Text style={styles.error}>
              {item.errorMessage ?? "Review this outcome before continuing."}
              {item.errorCode === null ? "" : ` · ${item.errorCode}`}
              {item.correlationId === null ? "" : ` · ${item.correlationId}`}
            </Text>
          )}
          {item.authPaused && (
            <Text style={styles.warning}>
              Sign in again, then sync pending proof to resume this unchanged
              command.
            </Text>
          )}
          {item.status === "conflict" && (
            <>
              <Text style={styles.warning}>
                Evidence is retained. Refresh the Delivery, compare remaining
                custody, then create a replacement with a new command identity.
              </Text>
              {item.replacedByConfirmationId === null &&
                onReviewConflict !== undefined && (
                  <Pressable
                    accessibilityRole="button"
                    onPress={() =>
                      void onReviewConflict(item).catch((error: unknown) =>
                        setReviewErrors((current) => ({
                          ...current,
                          [item.confirmationId]:
                            error instanceof Error
                              ? error.message
                              : "Current Delivery custody is unavailable.",
                        })),
                      )
                    }
                  >
                    <Text style={styles.link}>REVIEW AND REPLACE</Text>
                  </Pressable>
                )}
              {reviewErrors[item.confirmationId] !== undefined && (
                <Text style={styles.error}>
                  {reviewErrors[item.confirmationId]}
                </Text>
              )}
              {item.replacedByConfirmationId !== null && (
                <Text style={styles.warning}>
                  Replaced by {item.replacedByConfirmationId}
                </Text>
              )}
            </>
          )}
          {item.replacesConfirmationId !== null && (
            <Text style={styles.detail}>
              Replacement for {item.replacesConfirmationId}
            </Text>
          )}
          {item.response?.collection?.status === "cleared" && (
            <Text style={styles.positive}>
              COD {item.response.collection.payment_method.replaceAll("_", " ")}{" "}
              · PHP {item.response.collection.amount_collected} cleared · cash
              reconciliation{" "}
              {item.response.collection.cash_reconciliation_status ??
                "not required"}
            </Text>
          )}
          {item.response?.on_account_conversion?.status === "consumed" && (
            <Text style={styles.warning}>
              COD converted to On Account · PHP{" "}
              {item.response.on_account_conversion.amount}
            </Text>
          )}
          {item.status === "confirmed" &&
            (() => {
              const receipt = item.response?.delivery_receipt;
              if (receipt == null) return null;
              const current = receiptStates[receipt.delivery_receipt_id];
              const status = current?.status ?? receipt.status;
              return (
                <>
                  {status === "pending_document" && (
                    <Text style={styles.warning}>
                      Receipt unavailable — rendering in progress
                    </Text>
                  )}
                  {status === "unavailable" && (
                    <Text style={styles.error}>
                      Receipt rendering unavailable — background retry scheduled
                    </Text>
                  )}
                  {status === "ready" && current !== undefined && (
                    <Pressable
                      accessibilityRole="button"
                      onPress={() =>
                        current.accessUrl === null
                          ? undefined
                          : void Linking.openURL(current.accessUrl)
                      }
                    >
                      <Text style={styles.link}>
                        OPEN RECEIPT {current.number}
                      </Text>
                    </Pressable>
                  )}
                  {onRefreshReceipt !== undefined && (
                    <Pressable
                      accessibilityRole="button"
                      onPress={async () => {
                        const refreshed = await onRefreshReceipt(
                          receipt.delivery_receipt_id,
                        );
                        setReceiptStates((values) => ({
                          ...values,
                          [receipt.delivery_receipt_id]: refreshed,
                        }));
                      }}
                    >
                      <Text style={styles.link}>REFRESH RECEIPT</Text>
                    </Pressable>
                  )}
                </>
              );
            })()}
        </View>
      ))}
      {items?.some((item) =>
        ["pending_upload", "pending_confirmation", "upload_failed"].includes(
          item.status,
        ),
      ) === true && (
        <Pressable
          accessibilityRole="button"
          onPress={() => void sync()}
          style={styles.button}
        >
          <Text style={styles.buttonText}>
            {syncing ? "SYNCING…" : "SYNC PENDING PROOF"}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

function title(item: LocalDeliveryConfirmation): string {
  if (item.authPaused) return "Sign in required — Pending Sync retained";
  if (item.status === "confirmed") return "Delivery confirmed";
  if (item.status === "conflict")
    return "Confirmation conflict — review required";
  if (
    item.status === "forbidden" &&
    ["authentication_required", "invalid_token"].includes(item.errorCode ?? "")
  )
    return "Sign in required before sync";
  if (item.status === "forbidden") return "Confirmation forbidden";
  if (item.status === "upload_failed")
    return "Upload failed — evidence retained";
  return "Pending Sync";
}

const styles = StyleSheet.create({
  button: { backgroundColor: colors.ink, marginTop: 16, padding: 14 },
  buttonText: {
    color: colors.paper,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1,
  },
  detail: { color: colors.inkMuted, fontSize: 12 },
  error: { color: colors.red, fontSize: 12, marginTop: 6 },
  eyebrow: {
    color: colors.orange,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.4,
  },
  heading: {
    color: colors.ink,
    fontSize: 18,
    fontWeight: "700",
    marginVertical: 6,
  },
  item: {
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    marginTop: 12,
    paddingTop: 12,
  },
  link: { color: colors.orange, fontSize: 12, fontWeight: "700", marginTop: 8 },
  positive: { color: colors.green, fontSize: 12, marginTop: 6 },
  section: {
    borderTopColor: colors.ink,
    borderTopWidth: 1,
    margin: 20,
    paddingTop: 16,
  },
  warning: { color: colors.orange, fontSize: 12, marginTop: 6 },
});
