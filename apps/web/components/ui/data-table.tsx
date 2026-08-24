import type { ReactNode } from "react";

type DataTableProps = {
  children: ReactNode;
};

export function DataTable({ children }: DataTableProps) {
  return (
    <div className="data-table-wrap">
      <table className="data-table">{children}</table>
    </div>
  );
}
