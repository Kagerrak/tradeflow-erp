import { render, screen } from "@testing-library/react-native";

import { PlatformHome } from "./platform-home";

jest.mock("expo-crypto", () => ({
  randomUUID: () => "2b9ca5d3-35c7-4da3-bab7-832c66cbb53c",
}));

it("opens the warehouse Pick list from assigned work", async () => {
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
              capabilities: ["platform:read", "fulfillment:pick"],
              display_name: "Warehouse Clerk",
              subject: "warehouse-mnl",
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

  expect(await screen.findByLabelText("Open pick list")).toBeOnTheScreen();
  expect(screen.getByText("4 TASKS")).toBeOnTheScreen();
});
