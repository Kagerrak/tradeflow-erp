import { WarehousePickingWorkbench } from "@/components/warehouse-picking-workbench";

export default async function PickingPage({
  searchParams,
}: {
  searchParams: Promise<{ fulfillmentOrderId?: string }>;
}) {
  const { fulfillmentOrderId = "" } = await searchParams;
  return (
    <WarehousePickingWorkbench initialFulfillmentOrderId={fulfillmentOrderId} />
  );
}
