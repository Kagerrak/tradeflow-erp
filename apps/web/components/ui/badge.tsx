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
  children: string;
  variant?: "default" | "success" | "warning" | "danger";
};

export function Badge({ children, variant = "default" }: BadgeProps) {
  const mapped = statusMap[children.toLowerCase()] ?? { variant };
  const label = mapped.label ?? children;
  return <span className={`badge badge-${mapped.variant}`}>{label}</span>;
}
