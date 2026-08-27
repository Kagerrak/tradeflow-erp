import type { ReactNode } from "react";

type PageHeaderProps = {
  actions?: ReactNode;
  description?: string;
  eyebrow?: string;
  tabs?: ReactNode;
  title: string;
};

export function PageHeader({
  actions,
  description,
  eyebrow,
  tabs,
  title,
}: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header-main">
        <div className="page-header-text">
          {eyebrow && <span className="page-header-eyebrow">{eyebrow}</span>}
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="page-header-actions">{actions}</div>}
      </div>
      {tabs && <div className="page-header-tabs">{tabs}</div>}
    </header>
  );
}
