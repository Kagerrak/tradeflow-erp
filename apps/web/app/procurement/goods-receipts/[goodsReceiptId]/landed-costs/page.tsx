import { ProcurementLandedCostWorkspace } from "../../../../../components/procurement-landed-cost-workspace";

export const dynamic = "force-dynamic";

export default async function ProcurementLandedCostPage({
  params,
}: {
  params: Promise<{ goodsReceiptId: string }>;
}) {
  const { goodsReceiptId } = await params;
  return <ProcurementLandedCostWorkspace goodsReceiptId={goodsReceiptId} />;
}
