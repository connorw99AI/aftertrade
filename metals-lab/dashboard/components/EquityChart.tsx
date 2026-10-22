"use client";
export function EquityChart({ points }: { points: [string, number][] }) {
  if (!points || points.length < 2) return <p className="empty">Not enough points for a curve.</p>;
  const vals = points.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const W = 600, H = 180;
  const d = points.map((p, i) => `${i === 0 ? "M" : "L"}${(i / (points.length - 1)) * W},${H - 12 - ((p[1] - lo) / span) * (H - 24)}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
         aria-label={`Equity from ${vals[0].toFixed(2)} to ${vals[vals.length - 1].toFixed(2)}`}>
      <path d={d} fill="none" stroke={up ? "#4FB09A" : "#D0654A"} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      <text x={6} y={14} fill="#A9A3BE" fontSize={11}>{hi.toFixed(0)}</text>
      <text x={6} y={H - 4} fill="#A9A3BE" fontSize={11}>{lo.toFixed(0)}</text>
    </svg>
  );
}
