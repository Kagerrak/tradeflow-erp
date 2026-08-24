import { CommercialApprovalQueue } from "@/components/commercial-approval-queue";

export default async function CommercialApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<{ orderId?: string }>;
}) {
  const { orderId } = await searchParams;
  return <CommercialApprovalQueue initialOrderId={orderId} />;
}
