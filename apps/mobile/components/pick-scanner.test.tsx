import { fireEvent, render, screen } from "@testing-library/react-native";

import { createMemoryPickCommandStore } from "../offline/pick-command-store";
import { PickScanner } from "./pick-scanner";

const fulfillmentOrderId = "f6a25d93-d412-474a-8e37-f23716579a88";
const lineId = "4a7f72bc-9172-455f-adca-5472c655e658";

async function fillPick() {
  await fireEvent.changeText(
    screen.getByLabelText("Fulfillment Order ID"),
    fulfillmentOrderId,
  );
  await fireEvent.changeText(
    screen.getByLabelText("Sales Order Line ID"),
    lineId,
  );
  await fireEvent.changeText(screen.getByLabelText("Released version"), "2");
  await fireEvent.changeText(
    screen.getByLabelText("Pick quantity"),
    "1.000000",
  );
  await fireEvent.changeText(screen.getByLabelText("Unit"), "EA");
  await fireEvent.changeText(
    screen.getByLabelText("Scan barcode"),
    "480000000003",
  );
}

it("queues an offline scan without claiming stock is staged", async () => {
  const fetch = jest.fn();
  const store = createMemoryPickCommandStore();
  const ids = ["5b914dde-9a1f-45d7-b7a9-cb5ff8a8b458", "stable-pick-key"];
  await render(
    <PickScanner
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => ids.shift() ?? "unused"}
      fetch={fetch}
      isOnline={async () => false}
      store={store}
    />,
  );
  await fillPick();
  await fireEvent.press(
    screen.getByRole("button", { name: "Queue Pick command" }),
  );

  expect(
    await screen.findByRole("header", {
      name: "Pending Sync — not staged",
    }),
  ).toBeOnTheScreen();
  expect(
    screen.getAllByText(
      /Available stock has not moved until the server acknowledges/i,
    )[0],
  ).toBeOnTheScreen();
  expect(fetch).not.toHaveBeenCalled();
  expect(await store.listPending()).toHaveLength(1);
});

it("loads the authoritative Warehouse queue and renders an explicit empty state", async () => {
  await render(
    <PickScanner
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => "22222222-2222-4222-8222-222222222222"}
      fetch={async () =>
        new Response(JSON.stringify({ items: [], total: 0 }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        })
      }
      isOnline={async () => true}
      store={createMemoryPickCommandStore()}
    />,
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Load released Warehouse work" }),
  );
  expect(
    await screen.findByRole("header", { name: "No released picks" }),
  ).toBeOnTheScreen();
});

it("hydrates Pending Sync after a process restart", async () => {
  const store = createMemoryPickCommandStore();
  await store.saveAndEnqueue(
    fulfillmentOrderId,
    {
      expected_fulfillment_version: 2,
      lines: [
        {
          line_id: lineId,
          quantity: "1.000000",
          selections: [{ barcode: "480000000003" }],
          unit_code: "EA",
        },
      ],
      pick_id: "5b914dde-9a1f-45d7-b7a9-cb5ff8a8b458",
    },
    "restart-key",
    "2026-07-29T02:00:00Z",
  );

  await render(
    <PickScanner
      accessToken="token"
      baseUrl="https://api.test"
      isOnline={async () => false}
      store={store}
    />,
  );
  expect(
    await screen.findByRole("header", {
      name: "Pending Sync — not staged",
    }),
  ).toBeOnTheScreen();
  expect(screen.getByDisplayValue("480000000003")).toBeOnTheScreen();
});

it("queues a reasoned manual tracked-identity fallback", async () => {
  const store = createMemoryPickCommandStore();
  await render(
    <PickScanner
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => "manual-pick-id"}
      isOnline={async () => false}
      store={store}
    />,
  );
  await fillPick();
  await fireEvent.press(
    screen.getByRole("button", {
      name: "Toggle authorized manual fallback",
    }),
  );
  await fireEvent.changeText(screen.getByLabelText("Lot identity"), "LOT-007");
  await fireEvent.changeText(
    screen.getByLabelText("Manual selection reason"),
    "Label damaged; supervisor verified lot",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Queue Pick command" }),
  );
  expect((await store.listPending())[0]?.command.lines[0]?.selections).toEqual([
    {
      lot_code: "LOT-007",
      manual_reason: "Label damaged; supervisor verified lot",
      quantity: "1.000000",
    },
  ]);
});

it("shows partial staging only after authoritative acknowledgement", async () => {
  const store = createMemoryPickCommandStore();
  const ids = ["5b914dde-9a1f-45d7-b7a9-cb5ff8a8b458", "stable-pick-key"];
  await render(
    <PickScanner
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => ids.shift() ?? "unused"}
      fetch={async () =>
        new Response(
          JSON.stringify({
            fulfillment_order_id: fulfillmentOrderId,
            lines: [],
            pick_id: "5b914dde-9a1f-45d7-b7a9-cb5ff8a8b458",
            picked_quantity_base: "1.000000",
            remaining_quantity_base: "1.000000",
            status: "partially_picked",
            version: 3,
          }),
          { headers: { "content-type": "application/json" }, status: 201 },
        )
      }
      isOnline={async () => true}
      store={store}
    />,
  );
  await fillPick();
  await fireEvent.press(
    screen.getByRole("button", { name: "Queue Pick command" }),
  );

  expect(
    await screen.findByRole("header", { name: "Partial pick staged" }),
  ).toBeOnTheScreen();
  expect(screen.getByText("1.000000 base units remain")).toBeOnTheScreen();
  expect(
    screen.queryByRole("header", { name: "Pending Sync — not staged" }),
  ).not.toBeOnTheScreen();
});

it.each([
  [403, "operational_scope_required", "Picking access denied"],
  [409, "optimistic_version_conflict", "Pick needs review"],
  [409, "serial_already_picked", "Scan denied"],
  [409, "pick_reversed", "Pick reversed"],
] as const)("renders the explicit %s state", async (status, code, heading) => {
  const store = createMemoryPickCommandStore();
  const ids = ["5b914dde-9a1f-45d7-b7a9-cb5ff8a8b458", "stable-pick-key"];
  await render(
    <PickScanner
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => ids.shift() ?? "unused"}
      fetch={async () =>
        new Response(
          JSON.stringify({
            error: {
              code,
              correlation_id: "support-reference",
              message: code,
            },
          }),
          { headers: { "content-type": "application/json" }, status },
        )
      }
      isOnline={async () => true}
      store={store}
    />,
  );
  await fillPick();
  await fireEvent.press(
    screen.getByRole("button", { name: "Queue Pick command" }),
  );
  expect(
    await screen.findByRole("header", { name: heading }),
  ).toBeOnTheScreen();
});
