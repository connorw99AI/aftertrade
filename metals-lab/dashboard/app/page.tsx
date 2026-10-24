"use client";
import Link from "next/link";
import { BotControls, StatusBadge, VerdictPill } from "@/components/Bits";
import { Creature, CREATURES, Mood, SideIcon } from "@/components/Creature";
import { useSummary } from "@/components/Shell";
import { Any, money } from "@/lib/api";

function mood(b: Any): Mood {
  if (b.status === "STOPPED" || b.status === "PAUSED" || b.status === "RETIRED") return "asleep";
  if (b.status === "PAUSED_RISK" || b.stopped_for_day === "daily loss stop") return "hurt";
  if (b.open_positions.length) return "busy";
  if (b.today_pnl > 0) return "happy";
  return "idle";
}

function BotCard({ b }: { b: Any }) {
  const inTrade = b.open_positions.length > 0;
  // only the metals this bot may trade (a gold-only bot has nothing to say about silver), plus any metal it is in a trade on
  const roles = Object.keys(b.verdicts || {}).filter((r) => b.metals === "both" || r.toLowerCase() === b.metals
    || b.open_positions.some((p: Any) => p.role === r));
  const tone = b.status === "RETIRED" ? "card-retired" : b.stopped_for_day ? "card-stopped" : inTrade ? "card-trade" : b.status !== "RUNNING" ? "card-idle" : "";
  return (
    <Link href={`/bot?id=${b.bot_id}`} className={`botcard bc-${b.side} ${tone}`}>
      <div className="botcard-top">
        <span className="botcard-tags"><span className="tf-tag">{b.timeframe}</span>{b.status !== "RUNNING" && <StatusBadge status={b.status} />}</span>
        <SideIcon bot={b.bot_id} />
      </div>
      <div className="botcard-art"><Creature bot={b.bot_id} size={150} mood={mood(b)} scene /></div>
      <div className="botcard-name">
        <b>{CREATURES[b.bot_id]?.name}</b>
        <span className="muted">{b.bot_id} · {b.name}</span>
      </div>
      {b.status === "RETIRED" && <div className="retired-note">Retired{b.pause_reason ? `: ${b.pause_reason}` : ""}</div>}
      {b.stopped_for_day && <div className="daystop">Stopped for today: {b.stopped_for_day}</div>}
      <div className="botcard-verdicts">
        {roles.length === 0 && <span className="muted small">Waiting for the first bar…</span>}
        {roles.map((r) => (
          <div key={r} className="vrow"><span className={`metal metal-${r}`}>{r === "GOLD" ? "Gold" : "Silver"}</span><VerdictPill verdict={b.verdicts[r]?.verdict} /></div>
        ))}
      </div>
      {inTrade && (
        <ul className="botcard-trades">
          {b.open_positions.map((p: Any) => (
            <li key={p.position_key}>{p.side === "LONG" ? "▲" : "▼"} {p.side.toLowerCase()} {p.volume_open} {p.symbol} @ {Number(p.entry_price).toFixed(p.symbol.startsWith("XAG") ? 3 : 2)}</li>
          ))}
          <li className={b.unrealized >= 0 ? "gain" : "loss"}>Open P&amp;L {money(b.unrealized, true)}</li>
        </ul>
      )}
      <div className="botcard-stats">
        <div><span>Equity</span><b className="num">{money(b.equity)}</b></div>
        <div><span>Today</span><b className={`num ${b.today_pnl >= 0 ? "gain" : "loss"}`}>{money(b.today_pnl, true)}</b></div>
        <div><span>Trades today</span><b className="num">{b.entries_today}{b.max_entries_per_day ? ` / ${b.max_entries_per_day}` : ""}</b></div>
      </div>
      <BotControls bot={b.bot_id} status={b.status} hasOpen={inTrade} />
    </Link>
  );
}

export default function Overview() {
  const sum = useSummary();
  if (!sum) return <p className="empty">Loading…</p>;
  const shadow = sum.bots.some((b: Any) => b.shadow);
  return (
    <>
      <div className="page-head">
        <h1>Overview</h1>
        <p className="muted">Click a creature for its full view: live checklist, trades, review and settings.
          {shadow ? " Shadow mode: amounts are what each bot would get once you arm." : ""}</p>
      </div>
      <div className="botgrid">
        {sum.bots.map((b: Any) => <BotCard key={b.bot_id} b={b} />)}
      </div>
    </>
  );
}
