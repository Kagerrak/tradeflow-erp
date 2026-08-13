import { ProcurementGoodsReceiptWorkspace } from "../../../../../components/procurement-goods-receipt-workspace";

export const dynamic = "force-dynamic";

export default async function ProcurementGoodsReceiptPage({
  params,
}: {
  params: Promise<{ purchaseOrderId: string }>;
}) {
  const { purchaseOrderId } = await params;
  return <ProcurementGoodsReceiptWorkspace purchaseOrderId={purchaseOrderId} />;
}
