import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";

import { AssignedDeliveryList } from "../components/assigned-delivery-list";
import { createAssignedDeliveryCache } from "../offline/assigned-delivery-database";
import type { AssignedDeliveryCache } from "../offline/assigned-delivery-cache";

export default function Deliveries() {
  const [cache, setCache] = useState<AssignedDeliveryCache | null>(null);
  const accessToken = process.env.EXPO_PUBLIC_TRADEFLOW_TEST_ACCESS_TOKEN;
  const subject = tokenSubject(accessToken) ?? "signed-out";

  useEffect(() => {
    void createAssignedDeliveryCache(
      `tradeflow-deliveries-${subject.replaceAll(/[^a-zA-Z0-9_-]/g, "_")}.db`,
    ).then(async (nextCache) => {
      await nextCache.initialize();
      setCache(nextCache);
    });
  }, [subject]);

  if (cache === null) {
    return (
      <View
        accessibilityLabel="Opening assigned Delivery cache"
        accessibilityRole="progressbar"
      >
        <ActivityIndicator />
      </View>
    );
  }
  return (
    <AssignedDeliveryList
      accessToken={accessToken}
      baseUrl={
        process.env.EXPO_PUBLIC_TRADEFLOW_API_URL ?? "http://127.0.0.1:8000"
      }
      cache={cache}
      subject={subject}
    />
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
