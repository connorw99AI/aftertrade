import { ReactNode } from "react";

export function Stat({ label, value, sub, children }: { label: string; value: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <b className="stat-value">{value}</b>
      {sub != null && <span className="stat-sub">{sub}</span>}
      {children}
    </div>
  );
}

export function Stats({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`stats ${className}`}>{children}</div>;
}
