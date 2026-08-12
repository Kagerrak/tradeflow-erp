import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import { useState } from "react";

import {
  DeliveryPartitionEditor,
  createInitialPartitions,
} from "./delivery-partition-editor";

const delivery = {
  assignedTo: "delivery-mnl",
  collectionAmountDue: null,
  collectionRequired: false,
  deliveryAddress: { city: "Manila" },
  deliveryId: "8a8e9f4d-cb22-4c51-9fd7-30995bf9abef",
  evidenceRequirements: ["signature"],
  fulfillmentOrderId: "765b5ab6-7f39-4671-8561-747755641016",
  lines: [
    {
      deliveryLineId: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
      identityPositions: [],
      lineId: "4af0c99a-b55d-4f68-bf34-6f0805630032",
      lotSelections: [],
      quantityBase: "2.000000",
      serialNumbers: [],
      skuCode: "JUICE-1L",
      skuId: "37989314-b1b7-4bea-9c68-b6390ddae80f",
      skuName: "Mango Juice 1L",
    },
  ],
  paymentTimingPolicy: "prepaid" as const,
  recipientName: "Ana Santos",
  status: "dispatched" as const,
  version: 1,
};

it("partitions a physical Delivery Line exactly without floating point math", async () => {
  let observed = createInitialPartitions(delivery);
  function Harness() {
    const [value, setValue] = useState(createInitialPartitions(delivery));
    return (
      <DeliveryPartitionEditor
        delivery={delivery}
        onChange={(next) => {
          observed = next;
          setValue(next);
        }}
        value={value}
      />
    );
  }
  await render(<Harness />);

  await fireEvent.changeText(
    screen.getByLabelText("Mango Juice 1L Accepted quantity"),
    "1.333333",
  );
  await fireEvent.changeText(
    screen.getByLabelText("Mango Juice 1L Damaged quantity"),
    "0.666667",
  );
  await waitFor(() =>
    expect(observed).toMatchObject([
      {
        accepted_quantity_base: "1.333333",
        damaged_quantity_base: "0.666667",
        delivery_line_id: "d5de5a26-47c8-4f06-9b58-aa85d2e8a1d9",
      },
    ]),
  );
  expect(screen.getByText("BALANCED · READY TO SAVE")).toBeOnTheScreen();
});

it("makes an over-partition visible before durable capture", async () => {
  const value = createInitialPartitions(delivery);
  value[0]!.refused_quantity_base = "1";
  await render(
    <DeliveryPartitionEditor
      delivery={delivery}
      onChange={() => {}}
      value={value}
    />,
  );
  await waitFor(() =>
    expect(
      screen.getByText("OUTCOMES MUST EQUAL DISPATCHED"),
    ).toBeOnTheScreen(),
  );
});

it("derives tracked aggregates and requires one whole outcome per serial", async () => {
  const tracked = {
    ...delivery,
    lines: [
      {
        ...delivery.lines[0]!,
        identityPositions: [
          {
            deliveryLineIdentityAllocationId:
              "6d2416e4-22bb-4a18-8aec-acde396fa705",
            expirationDate: null,
            kind: "serial" as const,
            lotCode: null,
            quantityBase: "1.000000",
            serialNumber: "SN-001",
          },
        ],
        quantityBase: "1.000000",
        serialNumbers: ["SN-001"],
      },
    ],
  };
  let observed = createInitialPartitions(tracked);
  function Harness() {
    const [value, setValue] = useState(createInitialPartitions(tracked));
    return (
      <DeliveryPartitionEditor
        delivery={tracked}
        onChange={(next) => {
          observed = next;
          setValue(next);
        }}
        value={value}
      />
    );
  }
  await render(<Harness />);
  expect(
    screen.queryByLabelText("Mango Juice 1L Accepted quantity"),
  ).not.toBeOnTheScreen();
  await fireEvent.changeText(
    screen.getByLabelText("Serial SN-001 Accepted quantity"),
    "0.500000",
  );
  await fireEvent.changeText(
    screen.getByLabelText("Serial SN-001 Damaged quantity"),
    "0.500000",
  );
  expect(screen.getByText("CHOOSE ONE OUTCOME PER SERIAL")).toBeOnTheScreen();
  expect(observed[0]).toMatchObject({
    accepted_quantity_base: "0.500000",
    damaged_quantity_base: "0.500000",
  });

  await fireEvent.changeText(
    screen.getByLabelText("Serial SN-001 Accepted quantity"),
    "0",
  );
  await fireEvent.changeText(
    screen.getByLabelText("Serial SN-001 Damaged quantity"),
    "1.000000",
  );
  expect(screen.getByText("BALANCED · READY TO SAVE")).toBeOnTheScreen();
  expect(observed[0]).toMatchObject({
    accepted_quantity_base: "0.000000",
    damaged_quantity_base: "1.000000",
  });
});
