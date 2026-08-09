import { fireEvent, render, screen } from "@testing-library/react-native";

import { createMemoryAssignedDeliveryCache } from "../offline/assigned-delivery-cache";
import { AssignedDeliveryList } from "./assigned-delivery-list";

const delivery = {
  assigned_to: "delivery-mnl",
  collection_amount_due: "224.00",
  collection_required: true,
  delivery_address: {
    city: "Manila",
    line_1: "100 Payment Street",
    postal_code: "1000",
    region: "NCR",
  },
  delivery_id: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
  evidence_requirements: ["recipient_name", "signature"],
  fulfillment_order_id: "765b5ab6-7f39-4671-8561-747755641016",
  lines: [
    {
      delivery_line_id: "8334fd3c-8940-46d3-92cf-21d081b987d4",
      identity_positions: [
        {
          delivery_line_identity_allocation_id:
            "5eea1b5f-ec21-4d1c-8888-7348f523bbbe",
          lot_code: null,
          quantity_base: "1.000000",
          serial_number: "SN-001",
          tracking_policy: "serial",
        },
        {
          delivery_line_identity_allocation_id:
            "3dbf77d4-c5c8-42f1-b40c-04e31d1f62ab",
          lot_code: null,
          quantity_base: "1.000000",
          serial_number: "SN-002",
          tracking_policy: "serial",
        },
      ],
      line_id: "4af0c99a-b55d-4f68-bf34-6f0805630032",
      lot_selections: [],
      quantity_base: "2.000000",
      serial_numbers: ["SN-001", "SN-002"],
      sku_code: "JUICE-1L",
      sku_id: "37989314-b1b7-4bea-9c68-b6390ddae80f",
      sku_name: "Mango Juice 1L",
    },
  ],
  payment_timing_policy: "cash_on_delivery",
  recipient_name: "Prepaid Retail Customer",
  status: "dispatched",
  version: 1,
};

it("hydrates an authorized Delivery snapshot for offline read-only work", async () => {
  const cache = createMemoryAssignedDeliveryCache();
  const online = await render(
    <AssignedDeliveryList
      accessToken="token"
      baseUrl="https://api.test"
      cache={cache}
      createId={() => "delivery-correlation"}
      fetch={async () =>
        new Response(JSON.stringify({ items: [delivery], total: 1 }), {
          headers: {
            "Content-Type": "application/json",
            ETag: '"delivery-v1"',
          },
          status: 200,
        })
      }
      isOnline={async () => true}
      subject="delivery-mnl"
    />,
  );
  expect(
    await screen.findByRole("header", { name: "Prepaid Retail Customer" }),
  ).toBeOnTheScreen();
  expect(screen.getByText("SERIAL SN-001")).toBeOnTheScreen();
  expect(
    screen.getByText("CASH ON DELIVERY · PHP 224.00 · COLLECTION REQUIRED"),
  ).toBeOnTheScreen();
  await online.unmount();

  const onConfirm = jest.fn();
  await render(
    <AssignedDeliveryList
      accessToken="token"
      baseUrl="https://api.test"
      cache={cache}
      isOnline={async () => false}
      onConfirm={onConfirm}
      subject="delivery-mnl"
    />,
  );
  expect(
    await screen.findByRole("header", {
      name: "Cached task — authorization refresh required",
    }),
  ).toBeOnTheScreen();
  expect(screen.getByText("SERIAL SN-002")).toBeOnTheScreen();
  expect(screen.getByText(/Proof can be captured offline/i)).toBeOnTheScreen();
  fireEvent.press(screen.getByText("CAPTURE ACCEPTANCE"));
  expect(onConfirm).toHaveBeenCalledWith(
    expect.objectContaining({ deliveryId: delivery.delivery_id }),
  );
});

it("renders a server-side stale authorization rejection", async () => {
  const cache = createMemoryAssignedDeliveryCache();
  await cache.replace({
    cacheTag: '"delivery-v1"',
    deliveries: [],
    savedAt: "2026-08-01T09:00:00Z",
    subject: "delivery-mnl",
  });
  await render(
    <AssignedDeliveryList
      accessToken="token"
      baseUrl="https://api.test"
      cache={cache}
      createId={() => "revoked-correlation"}
      fetch={async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "delivery_assignment_required",
              correlation_id: "revoked-assignment",
              message: "The Delivery is no longer assigned to this user.",
            },
          }),
          { headers: { "Content-Type": "application/json" }, status: 403 },
        )
      }
      isOnline={async () => true}
      subject="delivery-mnl"
    />,
  );
  expect(
    await screen.findByRole("header", { name: "Delivery access revoked" }),
  ).toBeOnTheScreen();
  expect(screen.getByText("delivery_assignment_required")).toBeOnTheScreen();
  expect(await cache.load("delivery-mnl")).toBeNull();
});
