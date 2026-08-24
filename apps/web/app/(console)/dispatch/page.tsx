import { WarehouseDispatchWorkbench } from "@/components/warehouse-dispatch-workbench";

export default async function DispatchPage({
  searchParams,
}: {
  searchParams: Promise<{ fulfillmentOrderId?: string }>;
}) {
  const { fulfillmentOrderId = "" } = await searchParams;
  return (
    <WarehouseDispatchWorkbench
      initialFulfillmentOrderId={fulfillmentOrderId}
    />
  );
}
