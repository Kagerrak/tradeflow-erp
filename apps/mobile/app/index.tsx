import { PlatformHome } from "../components/platform-home";

export default function Index() {
  return (
    <PlatformHome
      accessToken={process.env.EXPO_PUBLIC_TRADEFLOW_TEST_ACCESS_TOKEN}
      baseUrl={
        process.env.EXPO_PUBLIC_TRADEFLOW_API_URL ?? "http://127.0.0.1:8000"
      }
    />
  );
}
