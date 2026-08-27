import type { ReactNode } from "react";

type ErrorStateProps = {
  action?: ReactNode;
  children?: ReactNode;
  correlationId?: string;
  severity?: "error" | "warning";
  title: string;
};

export function ErrorState({
  action,
  children,
  correlationId,
  severity = "error",
  title,
}: ErrorStateProps) {
  return (
    <div className={`error-state error-state-${severity}`} role="alert">
      <div className="error-state-body">
        <span className="error-state-icon" aria-hidden="true">
          {severity === "error" ? "⚠" : "ℹ"}
        </span>
        <div>
          <h3>{title}</h3>
          {children && <div className="error-state-children">{children}</div>}
          {correlationId && (
            <p className="error-state-reference">
              Reference: <code>{correlationId}</code>
            </p>
          )}
        </div>
      </div>
      {action && <div className="error-state-action">{action}</div>}
    </div>
  );
}
