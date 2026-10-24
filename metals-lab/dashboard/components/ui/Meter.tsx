/** How many of a set of conditions are met, one segment per condition. */
export function Meter({ label, met, total, title }: { label: string; met: number; total: number; title?: string }) {
  if (!total) return null;
  const tone = met >= total ? "full" : met / total >= 0.75 ? "near" : "";
  const segs = Math.min(total, 16);
  const on = Math.round((met / total) * segs);
  return (
    <div className={`meter ${tone} ${label ? "" : "no-label"}`} title={title ?? `${met} of ${total} entry conditions met`}>
      {label && <span>{label}</span>}
      <span className="meter-track" aria-hidden>{Array.from({ length: segs }, (_, i) => <i key={i} className={i < on ? "on" : ""} />)}</span>
      <span className="meter-count">{met}/{total}</span>
    </div>
  );
}

/** Progress toward a target, e.g. 18 of 50 trades. */
export function ProgressBar({ value, target, label }: { value: number; target: number; label?: string }) {
  const p = Math.max(0, Math.min(1, target ? value / target : 0));
  return (
    <div className="meter" title={label ?? `${value} of ${target}`} style={{ gridTemplateColumns: label ? "auto 1fr auto" : "1fr auto" }}>
      {label && <span>{label}</span>}
      <span className="meter-bar" aria-hidden><span className={p >= 1 ? "done" : ""} style={{ width: `${p * 100}%` }} /></span>
      <span className="meter-count">{value}/{target}</span>
    </div>
  );
}
