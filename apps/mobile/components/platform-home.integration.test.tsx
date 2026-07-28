import { render, screen } from "@testing-library/react-native";
import { loadPlatformSession } from "@tradeflow/platform-session";

import { CustomerDirectory } from "./customer-directory";
import { PlatformHome } from "./platform-home";

const runRealStack = process.env.TRADEFLOW_REAL_STACK === "1" ? it : it.skip;

runRealStack(
  "renders the authenticated API and PostgreSQL session through the native client",
  async () => {
    const accessToken = process.env.TRADEFLOW_REAL_STACK_ACCESS_TOKEN;
    const baseUrl =
      process.env.TRADEFLOW_REAL_STACK_API_URL ?? "http://127.0.0.1:8000";
    expect(typeof Request).toBe("function");
    const probe = await fetch(`${baseUrl}/health/live`);
    expect(probe.status).toBe(200);
    const state = await loadPlatformSession({
      accessToken,
      baseUrl,
      correlationId: "71194e04-e172-4e82-a3b6-ee79485c7217",
    });

    expect(state.kind).toBe("ready");

    await render(
      <PlatformHome
        accessToken={accessToken}
        baseUrl={baseUrl}
        createCorrelationId={() => "71194e04-e172-4e82-a3b6-ee79485c7217"}
      />,
    );

    expect(await screen.findByText("Field handoff is ready")).toBeOnTheScreen();
    expect(screen.getByText("Local Platform Operator")).toBeOnTheScreen();
    expect(screen.getByText("ready")).toBeOnTheScreen();
  },
);

runRealStack(
  "renders the Branch-scoped customer directory through the native client",
  async () => {
    await render(
      <CustomerDirectory
        accessToken={process.env.TRADEFLOW_REAL_STACK_SALES_TOKEN}
        baseUrl={
          process.env.TRADEFLOW_REAL_STACK_API_URL ?? "http://127.0.0.1:8000"
        }
        createCorrelationId={() => "89a81e99-09cf-4cf3-ae91-e36d63682297"}
      />,
    );

    expect(await screen.findByText("Real Stack Retail")).toBeOnTheScreen();
    expect(screen.queryByText("Cebu")).not.toBeOnTheScreen();
  },
);
