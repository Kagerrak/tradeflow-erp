import { InventoryWorkspace } from "../../components/inventory-workspace";
import Link from "next/link";

export default function InventoryPage() {
  return (
    <>
      <nav className="inventory-subnav" aria-label="Inventory operations">
        <Link href="/inventory/transfers">Transfers</Link>
      </nav>
      <InventoryWorkspace />
    </>
  );
}
