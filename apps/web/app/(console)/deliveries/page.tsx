import { DeliveryConfirmationWorkspace } from "@/components/delivery-confirmation-workspace";

export default async function DeliveriesPage({
  searchParams,
}: {
  searchParams: Promise<{ deliveryId?: string }>;
}) {
  const { deliveryId } = await searchParams;
  return <DeliveryConfirmationWorkspace initialDeliveryId={deliveryId} />;
}
