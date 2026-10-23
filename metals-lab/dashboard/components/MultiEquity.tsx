"use client";
import { CREATURES } from "./Creature";

/** Cumulative net P&L per bot on one chart, one line per bot in its creature colour. */
export function MultiEquity({ series }: { series: Record<string, [number, number][]> }) {
  const bots = Object.keys(series).filter((b) => series[b].length > 0);
  if (!bots.length) return <p className="empty">No closed trades yet.</p>;
  const all = bots.flatMap((b) => series[b]);
  const t0 = Math.min(...all.map((p) => p[0])), t1 = Math.max(...all.map((p) => p[0]));
  const lo = Math.min(0, ...all.map((p) => p[1])), hi = Math.max(0, ...all.map((p) => p[1]));
  const W = 900, H = 260, L = 50, B = 20;
  const x = (t: number) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - 10);
  const y = (v: number) => 10 + (1 - (v - lo) / (hi - lo || 1)) * (H - B - 10);
  return (
    <div>
      <svg className="multi-eq" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative net P&L per bot">
        <line x1={L} x2={W - 10} y1={y(0)} y2={y(0)} className="zero" />
        <text x={4} y={y(hi) + 4} className="axis">{hi.toFixed(0)}</text>
        <text x={4} y={y(0) + 4} className="axis">0</text>
        {lo < 0 && <text x={4} y={y(lo)} className="axis">{lo.toFixed(0)}</text>}
        {bots.map((b) => {
          const pts = series[b];
          const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
          return <path key={b} d={d} fill="none" stroke={CREATURES[b]?.colors.a ?? "#ccc"} strokeWidth={2} />;
        })}
      </svg>
      <div className="legend">
        {bots.map((b) => <span key={b}><i style={{ background: CREATURES[b]?.colors.a }} /> {CREATURES[b]?.name} {series[b].at(-1)?.[1].toFixed(2)}</span>)}
      </div>
    </div>
  );
}
