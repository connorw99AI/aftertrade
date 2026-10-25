"use client";
import Link from "next/link";
import { Creature, CREATURES } from "@/components/Creature";
import { Pnl } from "@/components/ui";
import { Any, minutes, money } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const dp = (sym: string) => (String(sym).startsWith("XAG") ? 3 : 2);

/** Top of the Lab: bots in a trade right now, and bots one condition (or less) from an entry. */
export function ActionNow({ bots }: { bots: Any[] }) {
  const { data } = usePoll<Any>("/api/positions", 5000);
  const open: Any[] = data?.positions ?? [];
  const next = bots.filter((b) => b.next_up)
    .sort((a, b) => Number(b.next_up.signal) - Number(a.next_up.signal) || a.next_up.missing.length - b.next_up.missing.length);
  return (
    <section className="panel action-now" aria-label="Action now">
      <div className="action-col">
        <h2>In a trade <span className="muted small">({open.length})</span></h2>
        {open.length === 0 ? <p className="muted small">No open trades.</p> : (
          <ul>{open.map((p) => (
            <li key={p.position_key}>
              <Link href={`/bot?id=${p.bot_id}`} className="who"><Creature bot={p.bot_id} size={26} mood="busy" /> {CREATURES[p.bot_id]?.name}</Link>
              <span className="what">{p.side === "LONG" ? "▲" : "▼"} {p.volume_open} {p.symbol} from {Number(p.entry_price).toFixed(dp(p.symbol))}</span>
              <span className="pl"><Pnl v={p.profit} /></span>
              <span className="tiny muted">{p.to_sl ? `stop ${money(p.to_sl.money, true)}` : "no stop"} · {p.to_tp ? `target ${money(p.to_tp.money, true)}` : "no target"} · open {p.minutes_open >= 0 ? minutes(p.minutes_open) : "–"}</span>
            </li>))}</ul>)}
      </div>
      <div className="action-col">
        <h2>About to enter <span className="muted small">({next.length})</span></h2>
        {next.length === 0 ? <p className="muted small">No bot is within one condition of an entry.</p> : (
          <ul>{next.map((b) => {
            const n = b.next_up;
            const word = b.bot_id.startsWith("S10") ? (b.side === "L" ? "silver-cheap pair" : "gold-cheap pair") : `${n.metal} ${b.side === "L" ? "long" : "short"}`;
            return (
              <li key={b.bot_id} className={n.signal ? "signal" : ""}>
                <Link href={`/bot?id=${b.bot_id}`} className="who"><Creature bot={b.bot_id} size={26} mood="idle" /> {CREATURES[b.bot_id]?.name}</Link>
                <span className="what">{word}</span>
                <span className="pl">{n.signal ? <b>Entry signal</b> : `${n.met} of ${n.total}`}</span>
                <span className="tiny muted">{n.signal ? "goes through sizing, the auditor and risk next" : `waiting on: ${n.missing[0]}`}</span>
              </li>);
          })}</ul>)}
      </div>
    </section>
  );
}
