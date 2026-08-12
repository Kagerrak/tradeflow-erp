import { render, screen } from "@testing-library/react-native";

import {
  createMemorySalesDraftBacking,
  createMemorySalesDraftStore,
} from "../offline/sales-draft-store";
import { SalesOrderDraftCapture } from "./sales-order-draft";

it("restores a durable Pending Sync draft after an offline process restart", async () => {
  const backing = createMemorySalesDraftBacking();
  const beforeRestart = createMemorySalesDraftStore(backing);
  await beforeRestart.saveAndEnqueue(
    {
      branch_id: "efad4205-5060-49fb-b752-3faca649ca6e",
      customer_id: "98481a1c-e493-41a6-851b-93142553ceab",
      expected_customer_version: 1,
      expected_price_list_version_id: "2903b3b0-608f-4caf-907a-0dd0886bb8f7",
      expected_pricing_date: "2026-07-29",
      delivery_address_version_id: "4d8ad09a-f96f-41b3-b30a-0af843353943",
      lines: [
        {
          expected_price_list_line_id: "d60c173e-efec-4b3a-b1c6-1e893e4cdfff",
          expected_unit_conversion_id: null,
          expected_unit_conversion_version: null,
          line_id: "a5551b35-34ff-4cb8-8062-d6386f7e4e25",
          manual_override_unit_price: null,
          price_override_reason: null,
          quantity: "3.000000",
          sku_id: "4d209f00-0c57-49fc-9f0b-fc5cf082cb02",
          unit_code: "EA",
        },
      ],
      order_discount_amount: "0.00",
      payment_timing_override_reason: null,
      payment_timing_policy: null,
      sales_order_id: "323484f7-f3b5-4070-846f-83b9aad4fadb",
    },
    "restart-stable-key",
    "2026-07-29T01:00:00Z",
  );

  await render(
    <SalesOrderDraftCapture
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => "screen-identity"}
      fetch={async () => {
        throw new TypeError("offline");
      }}
      store={createMemorySalesDraftStore(backing)}
    />,
  );

  await screen.findByText("Pending Sync");
  expect(screen.getByText("OFFLINE / CACHED PRICING")).toBeOnTheScreen();
  expect(
    screen.getByText(
      "The command and idempotency key are stored durably on this device.",
    ),
  ).toBeOnTheScreen();
});
