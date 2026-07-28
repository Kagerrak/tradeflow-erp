import { InventoryDirectory } from "../components/inventory-directory";

export default function Inventory() {
  return (
    <InventoryDirectory
      accessToken={process.env.EXPO_PUBLIC_TRADEFLOW_TEST_ACCESS_TOKEN}
      baseUrl={
        process.env.EXPO_PUBLIC_TRADEFLOW_API_URL ?? "http://127.0.0.1:8000"
      }
    />
  );
}
