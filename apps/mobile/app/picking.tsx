import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";

import { PickScanner } from "../components/pick-scanner";
import { createPickCommandStore } from "../offline/pick-command-database";
import type { PickCommandStore } from "../offline/pick-command-store";

export default function Picking() {
  const [store, setStore] = useState<PickCommandStore | null>(null);
  const accessToken = process.env.EXPO_PUBLIC_TRADEFLOW_TEST_ACCESS_TOKEN;

  useEffect(() => {
    const subject = tokenSubject(accessToken) ?? "signed-out";
    void createPickCommandStore(
      `tradeflow-picking-${subject.replaceAll(/[^a-zA-Z0-9_-]/g, "_")}.db`,
    ).then(async (nextStore) => {
      await nextStore.initialize();
      setStore(nextStore);
    });
  }, [accessToken]);

  if (store === null) {
    return (
      <View
        accessibilityLabel="Opening durable Pick outbox"
        accessibilityRole="progressbar"
      >
        <ActivityIndicator />
      </View>
    );
  }
  return (
    <PickScanner
      accessToken={accessToken}
      baseUrl={
        process.env.EXPO_PUBLIC_TRADEFLOW_API_URL ?? "http://127.0.0.1:8000"
      }
      store={store}
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
