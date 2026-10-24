import { ReactNode } from "react";

export type PillTone = "run" | "pause" | "stop" | "retired" | "kill" | "trade" | "info" | "strong" | "moderate" | "muted";

export function Pill({ tone = "muted", children, title }: { tone?: PillTone; children: ReactNode; title?: string }) {
  return <span className={`pill pill-${tone}`} title={title}>{children}</span>;
}
