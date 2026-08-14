import { InventoryTransferWorkspace } from "../../../components/inventory-transfer-workspace";
import Link from "next/link";

export default function InventoryTransfersPage() {
  return (
    <>
      <nav className="inventory-subnav" aria-label="Inventory operations">
        <Link href="/inventory">Inventory</Link>
      </nav>
      <InventoryTransferWorkspace />
    </>
  );
}
