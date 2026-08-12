import { fireEvent, render, screen } from "@testing-library/react-native";

import { CustomerDirectory } from "./customer-directory";

const props = {
  accessToken: "signed-token",
  baseUrl: "https://api.tradeflow.test",
  createCorrelationId: () => "2b9ca5d3-35c7-4da3-bab7-832c66cbb53c",
};

it("shows progress while loading scoped customer accounts", async () => {
  await render(
    <CustomerDirectory
      {...props}
      fetch={async () => await new Promise<Response>(() => undefined)}
    />,
  );
  expect(
    screen.getByLabelText("Loading scoped customer accounts"),
  ).toBeOnTheScreen();
});

it("shows the empty scoped directory", async () => {
  await render(
    <CustomerDirectory
      {...props}
      fetch={async () =>
        new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })
      }
    />,
  );
  expect(
    await screen.findByText("No accounts in your scope"),
  ).toBeOnTheScreen();
});

it("searches and renders a customer account", async () => {
  await render(
    <CustomerDirectory
      {...props}
      fetch={async () =>
        new Response(
          JSON.stringify({
            items: [
              {
                account_number: "MNL-0042",
                branch_id: "branch-mnl",
                credit_hold: false,
                customer_id: "customer-42",
                legal_name: "Northstar Retail",
                payment_timing_policy: "cash_on_delivery",
                status: "active",
                version: 1,
              },
            ],
            total: 1,
          }),
          { status: 200 },
        )
      }
    />,
  );
  expect(await screen.findByText("Northstar Retail")).toBeOnTheScreen();
  expect(screen.getByText("Cash on delivery")).toBeOnTheScreen();
});

it("explains forbidden customer access", async () => {
  await render(
    <CustomerDirectory
      {...props}
      fetch={async () => new Response("{}", { status: 403 })}
    />,
  );
  expect(
    await screen.findByText("Customer access is not assigned"),
  ).toBeOnTheScreen();
});

it("shows validation guidance for a rejected search", async () => {
  await render(
    <CustomerDirectory
      {...props}
      fetch={async () => new Response("{}", { status: 422 })}
    />,
  );
  expect(
    await screen.findByText("Use at least two search characters"),
  ).toBeOnTheScreen();
});

it("retries an unavailable directory", async () => {
  let count = 0;
  await render(
    <CustomerDirectory
      {...props}
      fetch={async () => {
        count += 1;
        if (count === 1) throw new TypeError("offline");
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200,
        });
      }}
    />,
  );
  expect(
    await screen.findByText("Directory temporarily unavailable"),
  ).toBeOnTheScreen();
  await fireEvent.press(
    screen.getByRole("button", { name: "Retry customer search" }),
  );
  expect(
    await screen.findByText("No accounts in your scope"),
  ).toBeOnTheScreen();
});
