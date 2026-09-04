import { redirect } from "next/navigation";

export default function SalesOrdersIndexPage() {
  redirect("/sales-orders/approvals");
}
