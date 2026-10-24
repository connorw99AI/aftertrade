"use client";
import { useEffect, useState } from "react";
import { Any } from "@/lib/api";
import { CREATURES } from "./Creature";

type Candle = { t: string; o: number; h: number; l: number; c: number; forming?: boolean };

const PAD_B = 22, PAD_T = 10;

/** Narrow screens get a smaller drawing space so axis text stays readable when scaled to the phone width. */
export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const q = window.matchMedia("(max-width: 640px)");
    const f = () => setNarrow(q.matches);
    f();
    q.addEventListener("change", f);
    return () => q.removeEventListener("change", f);
  }, []);
  return narrow;
}

function fmtTime(iso: string, tf: string) {
  const d = new Date(iso);
  if (tf === "D1") return d.toLocaleDateString("en-GB", { month: "short", day: "numeric" });
  if (tf === "M1" || tf === "M5") return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

export function CandleChart({ data, overlay = [] }: { data: Any; overlay?: { label: string; price: number }[] }) {
  const narrow = useNarrow();
  const W = narrow ? 400 : 760, H = narrow ? 300 : 320, PAD_R = narrow ? 58 : 64;
  const cs: Candle[] = data?.candles ?? [];
  if (cs.length < 2) return <p className="empty">No price data yet for this timeframe.</p>;
  const digits: number = data.digits ?? 2;
  const pos: Any[] = data.positions ?? [];
  const levels = pos.flatMap((p) => [p.entry, p.sl, p.tp]).filter((v) => typeof v === "number" && v > 0);
  const lo = Math.min(...cs.map((c) => c.l), ...levels);
  const hi = Math.max(...cs.map((c) => c.h), ...levels);
  const span = hi - lo || 1;
  const plotW = W - PAD_R, plotH = H - PAD_B - PAD_T;
  const y = (v: number) => PAD_T + (1 - (v - lo) / span) * plotH;
  const step = plotW / cs.length;
  const bodyW = Math.max(1, step * 0.62);
  const ticks = Array.from({ length: 5 }, (_, i) => lo + (span * i) / 4);
  const timeIdx = narrow ? [0, cs.length - 1] : [0, Math.floor(cs.length / 3), Math.floor((2 * cs.length) / 3), cs.length - 1];
  const last = cs[cs.length - 1];
  const bid: number | null = data.bid;
  return (
    <svg className="candles" viewBox={`0 0 ${W} ${H}`} role="img"
         aria-label={`${data.symbol} ${data.timeframe} chart, last ${last.c.toFixed(digits)}, range ${lo.toFixed(digits)} to ${hi.toFixed(digits)}`}>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={0} x2={plotW} y1={y(v)} y2={y(v)} className="grid" />
          <text x={plotW + 6} y={y(v) + 4} className="axis">{v.toFixed(digits)}</text>
        </g>
      ))}
      {cs.map((c, i) => {
        const x = i * step + step / 2;
        const up = c.c >= c.o;
        const top = y(Math.max(c.o, c.c)), bot = y(Math.min(c.o, c.c));
        return (
          <g key={c.t} className={`${up ? "up" : "down"}${c.forming ? " forming" : ""}`}>
            <line x1={x} x2={x} y1={y(c.h)} y2={y(c.l)} className="wick" />
            <rect x={x - bodyW / 2} y={top} width={bodyW} height={Math.max(1, bot - top)} className="body" />
          </g>
        );
      })}
      {pos.map((p, i) => {
        // each bot's lines end in its own creature, so they read without labels; hover for the detail
        const who = CREATURES[p.bot_id]?.name ?? p.bot_id;
        const lines: [string, number | null, string][] = [["entry", p.entry, `${who}: ${p.side.toLowerCase()} ${p.volume} lots from ${Number(p.entry).toFixed(digits)}`],
          ["sl", p.sl, `${who}: stop ${Number(p.sl).toFixed(digits)}`], ["tp", p.tp, `${who}: target ${Number(p.tp).toFixed(digits)}`]];
        const ix = plotW - 26 - (i % 4) * 26;
        return lines.filter(([, v]) => v && v > 0).map(([k, v, label]) => {
          const sz = k === "entry" ? 22 : 15;
          return (
            <g key={`${i}-${k}`} className={`lvl lvl-${k}`}>
              <title>{label}</title>
              <line x1={0} x2={plotW} y1={y(v as number)} y2={y(v as number)} />
              <image href={`/creatures/${p.bot_id}.png`} x={ix + (22 - sz) / 2} y={y(v as number) - sz / 2} width={sz} height={sz} />
              {k !== "entry" && <text x={ix - 4} y={y(v as number) - 3} textAnchor="end">{k === "sl" ? "stop" : "target"}</text>}
            </g>
          );
        });
      })}
      {overlay.map((o, i) => {
        const inside = o.price >= lo && o.price <= hi;
        const yy = inside ? y(o.price) : o.price > hi ? PAD_T + 10 + i * 12 : H - PAD_B - 4 - i * 12;
        return (
          <g key={`ov-${i}`} className="lvl lvl-overlay">
            {inside && <line x1={0} x2={plotW} y1={yy} y2={yy} />}
            <text x={plotW - 6} y={inside ? yy - 3 : yy} textAnchor="end">
              {inside ? "" : o.price > hi ? "↑ " : "↓ "}{o.label} {o.price.toFixed(digits)}
            </text>
          </g>
        );
      })}
      {bid !== null && bid >= lo && bid <= hi && (
        <g className="lvl lvl-bid">
          <line x1={0} x2={plotW} y1={y(bid)} y2={y(bid)} />
          <rect x={plotW + 2} y={y(bid) - 9} width={PAD_R - 4} height={16} rx={3} />
          <text x={plotW + 6} y={y(bid) + 3}>{bid.toFixed(digits)}</text>
        </g>
      )}
      {timeIdx.map((i) => (
        <text key={i} x={Math.min(plotW - 40, Math.max(0, i * step))} y={H - 6} className="axis">{fmtTime(cs[i].t, data.timeframe)}</text>
      ))}
    </svg>
  );
}
