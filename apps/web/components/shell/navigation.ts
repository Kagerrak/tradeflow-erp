export type NavSection = {
  label: string;
  items: NavItem[];
};

export type NavItem = {
  href: string;
  label: string;
  marker?: string;
};

export const navSections: NavSection[] = [
  {
    label: "Operations",
    items: [
      { href: "/demo", label: "Control desk", marker: "CD" },
      { href: "/sales-orders/new", label: "Sales orders", marker: "SO" },
      { href: "/sales-orders/approvals", label: "Approvals", marker: "AP" },
      { href: "/picking", label: "Picking", marker: "PK" },
      { href: "/dispatch", label: "Dispatch", marker: "DP" },
      { href: "/deliveries", label: "Deliveries", marker: "DL" },
    ],
  },
  {
    label: "Inventory",
    items: [
      { href: "/inventory", label: "Stock ledger", marker: "ST" },
      { href: "/inventory/transfers", label: "Transfers", marker: "TR" },
      { href: "/inventory/adjustments", label: "Adjustments", marker: "AD" },
    ],
  },
  {
    label: "Commercial",
    items: [
      { href: "/customers", label: "Customers", marker: "CU" },
      { href: "/finance", label: "Finance", marker: "FI" },
      { href: "/finance/credit-notes", label: "Credit notes", marker: "CN" },
      { href: "/finance/allocations", label: "Allocations", marker: "AL" },
      { href: "/finance/statement", label: "Statement", marker: "ST" },
      { href: "/finance/timeline", label: "Timeline", marker: "TL" },
      { href: "/payments", label: "Payments", marker: "PY" },
    ],
  },
  {
    label: "Procurement",
    items: [
      { href: "/procurement", label: "Procurement", marker: "PR" },
      { href: "/procurement/suppliers", label: "Suppliers", marker: "SU" },
      {
        href: "/procurement/purchase-requests",
        label: "Requests",
        marker: "RQ",
      },
      {
        href: "/procurement/purchase-orders",
        label: "Purchase orders",
        marker: "PO",
      },
    ],
  },
];

export function findNavItem(pathname: string): NavItem | undefined {
  for (const section of navSections) {
    for (const item of section.items) {
      if (item.href === pathname) return item;
      // Allow sub-paths to highlight parent nav items for grouped routes
      if (
        pathname.startsWith(item.href + "/") &&
        item.href !== "/" &&
        item.href.split("/").length > 1
      ) {
        return item;
      }
    }
  }
  return undefined;
}
