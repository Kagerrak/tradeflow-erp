import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const statusMap: Record<
  string,
  { label?: string; variant: "default" | "success" | "warning" | "danger" }
> = {
  active: { variant: "success" },
  approved: { variant: "success" },
  clear: { variant: "success" },
  draft: { variant: "default" },
  held: { variant: "danger" },
  inactive: { variant: "default" },
  inactive_label: { label: "Inactive", variant: "default" },
  on_hold: { label: "On hold", variant: "danger" },
  paid: { variant: "success" },
  pending: { variant: "warning" },
  prepaid: { variant: "success" },
  prospect: { variant: "warning" },
  rejected: { variant: "danger" },
};

type BadgeProps = {
  children: ReactNode;
  className?: string;
  variant?: "default" | "success" | "warning" | "danger";
};

export function Badge({
  children,
  className,
  variant = "default",
}: BadgeProps) {
  const mapped =
    typeof children === "string"
      ? (statusMap[children.toLowerCase()] ?? { variant })
      : { variant };
  const label = mapped.label ?? children;
  return (
    <span className={cn(`badge badge-${mapped.variant}`, className)}>
      {label}
    </span>
  );
}
