import { InventoryAdjustmentWorkspace } from "../../../components/inventory-adjustment-workspace";
import Link from "next/link";

export default function InventoryAdjustmentsPage() {
  return (
    <>
      <nav className="inventory-subnav" aria-label="Inventory operations">
        <Link href="/inventory">Inventory</Link>
      </nav>
      <InventoryAdjustmentWorkspace />
    </>
  );
}
