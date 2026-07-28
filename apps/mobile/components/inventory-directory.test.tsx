import { fireEvent, render, screen } from "@testing-library/react-native";

import { InventoryDirectory } from "./inventory-directory";

const ready = {
  items: [
    {
      available: "30.000000",
      base_currency: "PHP",
      base_stocking_unit: "EA",
      custody: "available",
      expiration_control: true,
      expiration_date: "2027-12-31",
      warehouse_inventory_value: "360.000000",
      location_code: "AVAILABLE",
      lot_code: "LOT-A",
      moving_average_unit_cost: "12.000000",
      on_hand: "30.000000",
      reserved: "0.000000",
      serial_numbers: [],
      sku_code: "COLA-330",
      sku_id: "d6a72680-6334-434d-8969-d2fc87da6397",
      sku_name: "Cola 330 mL",
      tracking_policy: "lot",
      warehouse_code: "MNL-01",
      warehouse_id: "6cadf528-a2ff-4d05-b25c-940c79b112ad",
    },
  ],
  total: 1,
};

it("shows scoped quantity and traceability", async () => {
  const fetch = jest.fn(
    async () =>
      new Response(JSON.stringify(ready), {
        headers: { "content-type": "application/json" },
        status: 200,
      }),
  );
  await render(
    <InventoryDirectory
      accessToken="token"
      baseUrl="https://api.test"
      createCorrelationId={() => "inventory-mobile"}
      fetch={fetch}
    />,
  );
  await screen.findByText("Cola 330 mL");
  expect(screen.getAllByText("30.000000")).toHaveLength(2);
  expect(screen.getByText("MNL-01 / AVAILABLE · EA")).toBeOnTheScreen();
  expect(
    screen.getByText(
      "MOVING AVG PHP 12.000000 · WAREHOUSE VALUE PHP 360.000000",
    ),
  ).toBeOnTheScreen();
});

it("shows forbidden scope guidance", async () => {
  await render(
    <InventoryDirectory
      accessToken="token"
      baseUrl="https://api.test"
      createCorrelationId={() => "inventory-mobile-error"}
      fetch={async () => new Response("{}", { status: 403 })}
    />,
  );
  await screen.findByText("Inventory access is not assigned");
  expect(screen.getByText(/inventory-mobile-error/)).toBeOnTheScreen();
});

it("recovers from an unavailable inventory service", async () => {
  let count = 0;
  await render(
    <InventoryDirectory
      accessToken="token"
      baseUrl="https://api.test"
      createCorrelationId={() => "inventory-mobile-error"}
      fetch={async () => {
        count += 1;
        if (count === 1) return new Response("{}", { status: 503 });
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200,
        });
      }}
    />,
  );
  await screen.findByText("Inventory temporarily unavailable");
  await fireEvent.press(screen.getByLabelText("Retry inventory search"));
  await screen.findByText("No stock in your Warehouse scope");
});

it("shows a genuine empty scoped result", async () => {
  await render(
    <InventoryDirectory
      accessToken="token"
      baseUrl="https://api.test"
      createCorrelationId={() => "inventory-empty"}
      fetch={async () =>
        new Response(JSON.stringify({ items: [], total: 0 }), {
          headers: { "content-type": "application/json" },
          status: 200,
        })
      }
    />,
  );
  await screen.findByText("No stock in your Warehouse scope");
});
