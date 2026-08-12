import { fireEvent, render, screen } from "@testing-library/react-native";

import { PlatformHome } from "./platform-home";

jest.mock("expo-crypto", () => ({
  randomUUID: () => "2b9ca5d3-35c7-4da3-bab7-832c66cbb53c",
}));

it("shows progress while checking the field session", async () => {
  await render(
    <PlatformHome
      accessToken="signed-token"
      baseUrl="https://api.tradeflow.test"
      fetch={async () => await new Promise<Response>(() => undefined)}
    />,
  );

  expect(
    screen.getByLabelText("Checking identity, API, and database"),
  ).toBeOnTheScreen();
});

it("shows the authoritative field session", async () => {
  await render(
    <PlatformHome
      accessToken="signed-token"
      baseUrl="https://api.tradeflow.test"
      fetch={async () =>
        new Response(
          JSON.stringify({
            database: "ready",
            service: "tradeflow-api",
            user: {
              capabilities: ["platform:read"],
              display_name: "Platform Tester",
              subject: "user-123",
            },
          }),
          {
            headers: { "content-type": "application/json" },
            status: 200,
          },
        )
      }
    />,
  );

  expect(await screen.findByText("Field handoff is ready")).toBeOnTheScreen();
  expect(screen.getByText("Platform Tester")).toBeOnTheScreen();
  expect(screen.getByText("ready")).toBeOnTheScreen();
});

it("gives an unauthenticated field operator a specific next action", async () => {
  await render(
    <PlatformHome
      accessToken={undefined}
      baseUrl="https://api.tradeflow.test"
    />,
  );

  expect(await screen.findByText("Sign in to continue")).toBeOnTheScreen();
  expect(screen.getByText("Open your identity provider")).toBeOnTheScreen();
});

it("explains a field capability denial", async () => {
  await render(
    <PlatformHome
      accessToken="signed-token"
      baseUrl="https://api.tradeflow.test"
      fetch={async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "capability_required",
              correlation_id: "2b9ca5d3-35c7-4da3-bab7-832c66cbb53c",
              message: "The 'platform:read' capability is required.",
            },
          }),
          {
            headers: { "content-type": "application/json" },
            status: 403,
          },
        )
      }
    />,
  );

  expect(
    await screen.findByText("Platform access is not assigned"),
  ).toBeOnTheScreen();
  expect(screen.getByText("Ask an operations administrator")).toBeOnTheScreen();
});

it("lets the field operator retry a temporarily unavailable platform", async () => {
  let requestCount = 0;
  await render(
    <PlatformHome
      accessToken="signed-token"
      baseUrl="https://api.tradeflow.test"
      fetch={async () => {
        requestCount += 1;
        if (requestCount === 1) {
          throw new TypeError("Network request failed");
        }
        return new Response(
          JSON.stringify({
            database: "ready",
            service: "tradeflow-api",
            user: {
              capabilities: ["platform:read"],
              display_name: "Platform Tester",
              subject: "user-123",
            },
          }),
          {
            headers: { "content-type": "application/json" },
            status: 200,
          },
        );
      }}
    />,
  );

  expect(
    await screen.findByText("TradeFlow is temporarily unavailable"),
  ).toBeOnTheScreen();

  await fireEvent.press(
    screen.getByRole("button", { name: "Retry connection" }),
  );

  expect(await screen.findByText("Field handoff is ready")).toBeOnTheScreen();
});

it("turns an unexpected client failure into an explained recovery state", async () => {
  await render(
    <PlatformHome
      accessToken="signed-token"
      baseUrl="https://api.tradeflow.test"
      fetch={() => Promise.reject(new Error("Malformed response"))}
    />,
  );

  expect(
    await screen.findByText("TradeFlow is temporarily unavailable"),
  ).toBeOnTheScreen();
  expect(
    screen.getByText("Check your connection and try again"),
  ).toBeOnTheScreen();
});
