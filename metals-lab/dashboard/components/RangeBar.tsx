"use client";
import { Any, money } from "@/lib/api";

const dp = (sym: string) => (String(sym).startsWith("XAG") ? 3 : 2);

/** Stop on the left, target on the right, entry and the current price as marks: distance to danger at a glance. */
export function RangeBar({ p }: { p: Any }) {
  const sl = Number(p.sl) || null, tp = Number(p.tp) || null, entry = Number(p.entry_price), now = p.price != null ? Number(p.price) : null;
  if (!sl) return <span className="err">no stop</span>;
  const right = tp ?? entry + (entry - sl) * 2; // no target: draw two stop-distances of room
  const at = (v: number) => `${Math.max(0, Math.min(1, (v - sl) / (right - sl || 1))) * 100}%`;
  const d = dp(p.symbol);
  return (
    <div title={`Stop ${sl.toFixed(d)} · entry ${entry.toFixed(d)} · now ${now?.toFixed(d) ?? "–"} · target ${tp ? tp.toFixed(d) : "none"}`}>
      <div className="range"><span className="range-track" /><i className="entry" style={{ left: at(entry) }} />{now != null && <i className="now" style={{ left: at(now) }} />}</div>
      <div className="range-ends"><span>{p.to_sl ? money(p.to_sl.money, true) : sl.toFixed(d)}</span><span>{tp ? (p.to_tp ? money(p.to_tp.money, true) : tp.toFixed(d)) : "no target"}</span></div>
    </div>
  );
}
