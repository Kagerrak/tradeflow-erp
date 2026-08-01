import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { randomUUID } from "expo-crypto";

import { AssignedDeliveryList } from "../components/assigned-delivery-list";
import { DeliveryConfirmationCapture } from "../components/delivery-confirmation-capture";
import { DeliveryConfirmationStatus } from "../components/delivery-confirmation-status";
import { createAssignedDeliveryCache } from "../offline/assigned-delivery-database";
import type { AssignedDeliveryCache } from "../offline/assigned-delivery-cache";
import { createDeliveryConfirmationStore } from "../offline/delivery-confirmation-database";
import type { DeliveryConfirmationStore } from "../offline/delivery-confirmation-store";
import { syncDeliveryConfirmations } from "../offline/delivery-confirmation-sync";
import type { AssignedDelivery } from "@tradeflow/delivery-dispatch";
import { createTradeFlowClient } from "@tradeflow/api-client";

export default function Deliveries() {
  const [cache, setCache] = useState<AssignedDeliveryCache | null>(null);
  const [confirmationStore, setConfirmationStore] =
    useState<DeliveryConfirmationStore | null>(null);
  const [selected, setSelected] = useState<AssignedDelivery | null>(null);
  const accessToken = process.env.EXPO_PUBLIC_TRADEFLOW_TEST_ACCESS_TOKEN;
  const subject = tokenSubject(accessToken) ?? "signed-out";

  useEffect(() => {
    const safeSubject = subject.replaceAll(/[^a-zA-Z0-9_-]/g, "_");
    void Promise.all([
      createAssignedDeliveryCache(`tradeflow-deliveries-${safeSubject}.db`),
      createDeliveryConfirmationStore(
        `tradeflow-confirmations-${safeSubject}.db`,
      ),
    ]).then(async ([nextCache, nextConfirmationStore]) => {
      await Promise.all([
        nextCache.initialize(),
        nextConfirmationStore.initialize(),
      ]);
      setCache(nextCache);
      setConfirmationStore(nextConfirmationStore);
    });
  }, [subject]);

  if (cache === null || confirmationStore === null) {
    return (
      <View
        accessibilityLabel="Opening assigned Delivery cache"
        accessibilityRole="progressbar"
      >
        <ActivityIndicator />
      </View>
    );
  }
  const baseUrl =
    process.env.EXPO_PUBLIC_TRADEFLOW_API_URL ?? "http://127.0.0.1:8000";
  return (
    <View style={{ flex: 1 }}>
      <AssignedDeliveryList
        accessToken={accessToken}
        baseUrl={baseUrl}
        cache={cache}
        onConfirm={setSelected}
        subject={subject}
      />
      {selected !== null && (
        <DeliveryConfirmationCapture
          delivery={selected}
          onSaved={() => setSelected(null)}
          store={confirmationStore}
        />
      )}
      <DeliveryConfirmationStatus
        onRefreshReceipt={async (receiptId) => {
          const client = createTradeFlowClient({
            accessToken: accessToken ?? "",
            baseUrl,
            correlationId: randomUUID(),
          });
          const detail = await client.GET(
            "/v1/delivery-receipts/{delivery_receipt_id}",
            { params: { path: { delivery_receipt_id: receiptId } } },
          );
          if (detail.data === undefined)
            throw new Error("Receipt detail unavailable.");
          let accessUrl: string | null = null;
          if (detail.data.status === "ready") {
            const access = await client.POST(
              "/v1/delivery-receipts/{delivery_receipt_id}/access",
              { params: { path: { delivery_receipt_id: receiptId } } },
            );
            accessUrl = access.data?.access_url ?? null;
          }
          return {
            accessUrl,
            number: detail.data.number,
            status: detail.data.status,
          };
        }}
        onSync={async () => {
          await syncDeliveryConfirmations({
            accessToken,
            baseUrl,
            createCorrelationId: randomUUID,
            store: confirmationStore,
          });
        }}
        store={confirmationStore}
      />
    </View>
  );
}

function tokenSubject(accessToken: string | undefined): string | null {
  if (accessToken === undefined) return null;
  try {
    const encoded = accessToken.split(".")[1];
    if (encoded === undefined) return null;
    const unpadded = encoded.replaceAll("-", "+").replaceAll("_", "/");
    const base64 = unpadded.padEnd(
      unpadded.length + ((4 - (unpadded.length % 4)) % 4),
      "=",
    );
    const payload = JSON.parse(globalThis.atob(base64)) as { sub?: unknown };
    return typeof payload.sub === "string" && payload.sub.length > 0
      ? payload.sub
      : null;
  } catch {
    return null;
  }
}
