import { money } from "@/lib/api";

/** A profit or loss: neutral number, coloured ▲ / ▼ and a sign, so direction never depends on colour or clashes with side colour. */
export function Pnl({ v, className = "" }: { v: number | null | undefined; className?: string }) {
  if (v === null || v === undefined || Number.isNaN(v)) return <span className={`pnl ${className}`}>–</span>;
  const dir = v > 0.004 ? "up" : v < -0.004 ? "down" : "flat";
  return (
    <span className={`pnl is-${dir} ${className}`}>
      {dir !== "flat" && <span className="dir" aria-hidden>{dir === "up" ? "▲" : "▼"}</span>}{money(v, true)}
    </span>
  );
}

/** Percentage with the same treatment. */
export function PctDir({ v, digits = 1 }: { v: number | null | undefined; digits?: number }) {
  if (v === null || v === undefined || Number.isNaN(v)) return <span className="pnl">–</span>;
  const dir = v > 0 ? "up" : v < 0 ? "down" : "flat";
  return <span className={`pnl is-${dir}`}>{dir !== "flat" && <span className="dir" aria-hidden>{dir === "up" ? "▲" : "▼"}</span>}{v > 0 ? "+" : ""}{v.toFixed(digits)}%</span>;
}
