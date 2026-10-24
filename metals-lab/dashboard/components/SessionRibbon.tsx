"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Any } from "@/lib/api";
import { Creature, CREATURES } from "./Creature";

const DAY = 24 * 60;
const hhmm = (m: number) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

function nowMinute(serverTime?: string): number {
  const d = serverTime ? new Date(serverTime) : new Date();
  return d.getUTCHours() * 60 + d.getUTCMinutes();
}

/** Who is on duty: one 24-hour UTC strip per bot showing when it can open trades today, with a line at now. */
export function SessionRibbon({ bots, serverTime, storeKey }: { bots: Any[]; serverTime?: string; storeKey: string }) {
  const [open, setOpen] = useState(false);
  useEffect(() => { try { setOpen(window.localStorage.getItem(storeKey) === "1"); } catch { /* not stored */ } }, [storeKey]);
  const now = nowMinute(serverTime);
  const live = bots.filter((b) => b.status !== "RETIRED" && b.trading_window);
  const onDuty = live.filter((b) => b.trading_window.intervals.some(([a, z]: number[]) => a <= now && now < z));
  const next = live.flatMap((b) => b.trading_window.intervals.filter(([a]: number[]) => a > now).map(([a]: number[]) => ({ b, a })))
    .sort((x, y) => x.a - y.a)[0];
  return (
    <details className="panel ribbon" open={open} onToggle={(e) => {
      const o = (e.currentTarget as HTMLDetailsElement).open; setOpen(o);
      try { window.localStorage.setItem(storeKey, o ? "1" : "0"); } catch { /* not stored */ }
    }}>
      <summary>
        <b>On duty now: {onDuty.length} of {live.length}</b>
        <span className="muted small">
          {next ? ` · next to open: ${CREATURES[next.b.bot_id]?.name} at ${hhmm(next.a)} UTC` : " · no more windows open today"} · now {hhmm(now)} UTC
        </span>
      </summary>
      <div className="ribbon-body">
        <div className="ribbon-axis"><span /><div>{[0, 6, 12, 18, 24].map((h) => <span key={h}>{String(h).padStart(2, "0")}</span>)}</div></div>
        {live.map((b) => {
          const iv: number[][] = b.trading_window.intervals;
          const on = iv.some(([a, z]) => a <= now && now < z);
          return (
            <div key={b.bot_id} className={`ribbon-row ${b.status === "RUNNING" ? "" : "off"}`}>
              <Link href={`/bot?id=${b.bot_id}`} className="who"><Creature bot={b.bot_id} size={20} mood="still" />{CREATURES[b.bot_id]?.name}</Link>
              <div className={`ribbon-track bc-${b.side}`} title={`${b.trading_window.effective_utc}${on ? " · on duty now" : ""}`}>
                {iv.map(([a, z]) => <i key={a} style={{ left: `${(a / DAY) * 100}%`, width: `${((z - a) / DAY) * 100}%` }} />)}
                <span className="ribbon-now" style={{ left: `${(now / DAY) * 100}%` }} />
              </div>
            </div>
          );
        })}
        <p className="tiny muted">Times are UTC, today. A dimmed row is a bot that is not running. Windows combine the strategy&apos;s own session and its hour filter.</p>
      </div>
    </details>
  );
}
