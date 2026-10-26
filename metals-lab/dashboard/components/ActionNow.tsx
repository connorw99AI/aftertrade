"use client";
import Link from "next/link";
import { Creature, CREATURES } from "@/components/Creature";
import { Any } from "@/lib/api";

/** Top of the Lab: bots one condition (or less) from an entry. Bots in a trade show it on their own card, at the top. */
export function ActionNow({ bots }: { bots: Any[] }) {
  const next = bots.filter((b) => b.next_up)
    .sort((a, b) => Number(b.next_up.signal) - Number(a.next_up.signal) || a.next_up.missing.length - b.next_up.missing.length);
  return (
    <section className="panel action-now" aria-label="Action now">
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
