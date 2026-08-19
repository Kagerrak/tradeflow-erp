import { fireEvent, render, screen } from "@testing-library/react-native";

import { PaymentReceiptCapture } from "./payment-receipt-capture";
import { createMemoryPaymentReceiptStore } from "../offline/payment-receipt-store";

const branchId = "efad4205-5060-49fb-b752-3faca649ca6e";
const customerId = "98481a1c-e493-41a6-851b-93142553ceab";

it("queues an offline receipt without claiming it is cleared", async () => {
  const fetch = jest.fn();
  const store = createMemoryPaymentReceiptStore();
  await render(
    <PaymentReceiptCapture
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => "d2528c7a-c76a-42b1-a427-cde44d61f0b4"}
      fetch={fetch}
      isOnline={async () => false}
      store={store}
    />,
  );
  await fireEvent.changeText(screen.getByLabelText("Branch ID"), branchId);
  await fireEvent.changeText(
    screen.getByLabelText("Customer Account ID"),
    customerId,
  );
  await fireEvent.changeText(
    screen.getByLabelText("Received amount"),
    "224.00",
  );
  await fireEvent.press(screen.getByLabelText("Queue payment receipt"));
  await screen.findByText("Pending sync on this device");
  expect(
    screen.getByText(/not cleared money until the server acknowledges it/i),
  ).toBeOnTheScreen();
  expect(fetch).not.toHaveBeenCalled();
  expect(await store.listPending()).toHaveLength(1);
});

it("records online cash as cleared but still calls out reconciliation", async () => {
  const store = createMemoryPaymentReceiptStore();
  await render(
    <PaymentReceiptCapture
      accessToken="token"
      baseUrl="https://api.test"
      createId={() => "d2528c7a-c76a-42b1-a427-cde44d61f0b4"}
      fetch={async () =>
        new Response(
          JSON.stringify({
            allocated_amount: "0.00",
            amount: "224.00",
            application_state: "unapplied",
            available_for_coverage: "224.00",
            balance_version: 1,
            branch_id: branchId,
            cash_reconciliation_status: "unreconciled",
            cleared_amount: "224.00",
            currency: "PHP",
            customer_id: customerId,
            external_reference: null,
            external_reference_normalized: null,
            payment_method: "cash",
            payment_receipt_id: "d2528c7a-c76a-42b1-a427-cde44d61f0b4",
            received_at: "2026-07-29T02:00:00Z",
            recorded_by: "route-collector",
            reversal_id: null,
            sales_order_id: null,
            status: "cleared",
            unapplied_amount: "224.00",
            verified_by: null,
          }),
          {
            headers: { "content-type": "application/json" },
            status: 201,
          },
        )
      }
      isOnline={async () => true}
      store={store}
    />,
  );
  await fireEvent.changeText(screen.getByLabelText("Branch ID"), branchId);
  await fireEvent.changeText(
    screen.getByLabelText("Customer Account ID"),
    customerId,
  );
  await fireEvent.changeText(
    screen.getByLabelText("Received amount"),
    "224.00",
  );
  await fireEvent.press(screen.getByLabelText("Queue payment receipt"));
  await screen.findByRole("header", { name: "Cleared payment" });
  expect(screen.getByText(/224\.00 remains unapplied/)).toBeOnTheScreen();
  expect(
    screen.getByText(/it has not reduced an unrelated invoice/),
  ).toBeOnTheScreen();
  expect(screen.getByText(/Cash reconciliation remains due/)).toBeOnTheScreen();
  expect(await store.listPending()).toHaveLength(0);
});

it("explains the hold and retry boundary on mobile", async () => {
  await render(
    <PaymentReceiptCapture
      accessToken="token"
      baseUrl="https://api.test"
      isOnline={async () => false}
      store={createMemoryPaymentReceiptStore()}
    />,
  );
  expect(screen.getByText("Payment hold")).toBeOnTheScreen();
  expect(
    screen.getByText(
      /Clear payment, then run a new successful reservation retry/,
    ),
  ).toBeOnTheScreen();
});
