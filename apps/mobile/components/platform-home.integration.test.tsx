import { render, screen } from "@testing-library/react-native";
import { loadPlatformSession } from "@tradeflow/platform-session";
import {
  createSalesOrderDraft,
  loadOrderEntryReference,
} from "@tradeflow/sales-order-draft";
import { searchCustomerDirectory } from "@tradeflow/customer-directory";

import { CustomerDirectory } from "./customer-directory";
import { InventoryDirectory } from "./inventory-directory";
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
  "creates a priced Sales Order Draft through the generated native client",
  async () => {
    const accessToken = process.env.TRADEFLOW_REAL_STACK_SALES_TOKEN;
    const baseUrl =
      process.env.TRADEFLOW_REAL_STACK_API_URL ?? "http://127.0.0.1:8000";
    const customers = await searchCustomerDirectory({
      accessToken,
      baseUrl,
      correlationId: "60641d9f-bc41-455f-a461-714392cbb106",
      query: "Real Stack Retail",
    });
    expect(customers.kind).toBe("ready");
    if (customers.kind !== "ready") throw new Error("Expected customer scope.");
    const customer = customers.items[0]!;
    const reference = await loadOrderEntryReference({
      accessToken,
      baseUrl,
      branchId: customer.branchId,
      correlationId: "69d46ffc-9242-4b63-aa49-4f5ca601ef35",
      customerId: customer.customerId,
    });
    expect(reference.kind).toBe("ready");
    if (reference.kind !== "ready")
      throw new Error("Expected pricing reference.");
    const item = reference.reference.items[0]!;
    const result = await createSalesOrderDraft({
      accessToken,
      baseUrl,
      command: {
        branch_id: customer.branchId,
        customer_id: customer.customerId,
        expected_customer_version: reference.reference.customerVersion,
        expected_price_list_version_id: reference.reference.priceListVersionId,
        expected_pricing_date: reference.reference.pricingDate,
        delivery_address_version_id:
          reference.reference.addresses[0]!.addressVersionId,
        lines: [
          {
            expected_price_list_line_id: item.priceListLineId,
            expected_unit_conversion_id: item.unitConversionId,
            expected_unit_conversion_version: item.unitConversionVersion,
            line_id: "3326c04c-1bce-405c-af0d-15f09493f791",
            manual_override_unit_price: null,
            price_override_reason: null,
            quantity: "1.000000",
            sku_id: item.skuId,
            unit_code: item.unitCode,
          },
        ],
        order_discount_amount: "0.00",
        payment_timing_override_reason: null,
        payment_timing_policy: null,
        sales_order_id: "5d6ff6a5-c663-4c71-8d74-b54025173269",
      },
      correlationId: "b5a90cdc-0cfe-449e-8c09-6a36fb9a2cdd",
      idempotencyKey: "native-real-stack-sales-order",
    });
    expect(result.kind).toBe("saved");
    if (result.kind !== "saved") throw new Error("Expected saved Sales Order.");
    expect(result.draft).toMatchObject({
      paymentTimingPolicy: "on_account",
      priceListCode: "REAL-MNL-DEFAULT",
      status: "draft",
    });
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

runRealStack(
  "renders movement-derived Warehouse availability through the native client",
  async () => {
    await render(
      <InventoryDirectory
        accessToken={process.env.TRADEFLOW_REAL_STACK_SALES_TOKEN}
        baseUrl={
          process.env.TRADEFLOW_REAL_STACK_API_URL ?? "http://127.0.0.1:8000"
        }
        createCorrelationId={() => "48da84c0-e2a1-4c1e-938e-56e95cbcc311"}
      />,
    );

    expect(await screen.findByText("Real Stack Cola 330 mL")).toBeOnTheScreen();
    expect(screen.getByText("MNL-01 / REAL-AVAILABLE · EA")).toBeOnTheScreen();
    expect(screen.getByText("LOT · REAL-LOT-A · 2027-12-31")).toBeOnTheScreen();
  },
);
