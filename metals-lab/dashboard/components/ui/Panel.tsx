import { ReactNode } from "react";

export function Panel({ title, tools, className = "", children, as: H = "h3" }: {
  title?: ReactNode; tools?: ReactNode; className?: string; children: ReactNode; as?: "h2" | "h3";
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || tools) && <div className="panel-head">{title ? <H>{title}</H> : <span />}{tools}</div>}
      {children}
    </section>
  );
}
