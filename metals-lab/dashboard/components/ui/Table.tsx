import { ReactNode } from "react";

/** A table that scrolls sideways inside its own box instead of widening the page. */
export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className="table-wrap"><table className={className}>{children}</table></div>;
}
