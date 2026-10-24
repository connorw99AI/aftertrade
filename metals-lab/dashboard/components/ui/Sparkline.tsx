/** A small line chart. Pass `domain` to draw several on the same scale (small multiples). */
export type Domain = { t0: number; t1: number; lo: number; hi: number };

export function domainOf(series: [number, number][][]): Domain {
  const pts = series.flat();
  if (!pts.length) return { t0: 0, t1: 1, lo: -1, hi: 1 };
  return { t0: Math.min(...pts.map((p) => p[0])), t1: Math.max(...pts.map((p) => p[0])),
           lo: Math.min(0, ...pts.map((p) => p[1])), hi: Math.max(0, ...pts.map((p) => p[1])) };
}

export function Sparkline({ points, domain, height = 36, markers = [], label }: {
  points: [number, number][]; domain?: Domain; height?: number; markers?: number[]; label?: string;
}) {
  const W = 200, H = height;
  const d = domain ?? domainOf([points]);
  const x = (t: number) => ((t - d.t0) / (d.t1 - d.t0 || 1)) * W;
  const y = (v: number) => 2 + (1 - (v - d.lo) / (d.hi - d.lo || 1)) * (H - 4);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  return (
    <svg className="sparkline" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label ?? "trend"} style={{ height: H }}>
      {d.lo < 0 && d.hi > 0 && <line className="zero" x1={0} x2={W} y1={y(0)} y2={y(0)} />}
      {markers.map((t) => <line key={t} className="marker" x1={x(t)} x2={x(t)} y1={0} y2={H} />)}
      {points.length > 1 && <path className="line" d={path} />}
    </svg>
  );
}
