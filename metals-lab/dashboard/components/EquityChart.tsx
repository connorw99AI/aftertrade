"use client";
import { money } from "@/lib/api";

export type ChartMarker = { at: string; label: string };

/** Equity over time. Markers draw a dashed line where something changed, such as a new rule version. */
export function EquityChart({ points, markers = [] }: { points: [string, number][]; markers?: ChartMarker[] }) {
  if (!points || points.length < 2) return <p className="empty">Not enough points for a curve yet.</p>;
  const vals = points.map((p) => p[1]);
  const ts = points.map((p) => new Date(p[0]).getTime());
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const t0 = ts[0], t1 = ts[ts.length - 1];
  const span = hi - lo || 1;
  const W = 600, H = 180;
  const x = (t: number) => ((t - t0) / (t1 - t0 || 1)) * W;
  const y = (v: number) => H - 12 - ((v - lo) / span) * (H - 24);
  const d = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(ts[i]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const shown = markers.map((m) => ({ ...m, t: new Date(m.at).getTime() })).filter((m) => m.t >= t0 && m.t <= t1);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
         aria-label={`Equity from ${money(vals[0])} to ${money(vals[vals.length - 1])}${shown.length ? `, ${shown.length} rule change${shown.length > 1 ? "s" : ""} marked` : ""}`}>
      {shown.map((m) => <g key={m.at}><line className="marker" x1={x(m.t)} x2={x(m.t)} y1={0} y2={H} /><text className="marker-label" x={x(m.t) + 3} y={H - 4}>{m.label}</text></g>)}
      <path className="eq" d={d} />
      <text className="axis" x={6} y={14}>{money(hi)}</text>
      <text className="axis" x={6} y={H - 4}>{money(lo)}</text>
    </svg>
  );
}
