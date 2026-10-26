"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Any, money } from "@/lib/api";
import { botAction, StateToggle } from "./Bits";
import { CHARACTERS } from "./characters";
import { Creature, CREATURES, Mood, SideIcon, StateSymbol, stateSymbol } from "./Creature";
import { usePopups } from "./Shell";
import { Button, Menu, Meter, Pnl } from "./ui";

export function cardMood(b: Any, kill: boolean): Mood {
  if (b.status === "PAUSED_RISK" || b.stopped_for_day === "daily loss stop") return "hurt";
  if (kill || b.status === "STOPPED" || b.status === "PAUSED") return "still";
  if (b.open_positions.length) return "busy";
  if (b.today_pnl > 0) return "happy";
  return "idle";
}

/** Lean toward a new trade once, the moment it opens. */
function useOpenReaction(opens: Any[]): "long" | "short" | null {
  const prev = useRef<number | null>(null);
  const [react, setReact] = useState<"long" | "short" | null>(null);
  useEffect(() => {
    const n = opens.length;
    if (prev.current !== null && n > prev.current) {
      setReact(opens[opens.length - 1]?.side === "SHORT" ? "short" : "long");
      const t = setTimeout(() => setReact(null), 1500);
      prev.current = n;
      return () => clearTimeout(t);
    }
    prev.current = n;
  }, [opens]);
  return react;
}

/** One bot on the Lab bench. The card is an article: the art and the name are the link, the controls sit outside it. */
export function BotCard({ b, kill }: { b: Any; kill: boolean }) {
  const id: string = b.bot_id;
  const name = CREATURES[id]?.name ?? id;
  const inTrade = b.open_positions.length > 0;
  const react = useOpenReaction(b.open_positions);
  const coin = usePopups().filter((p) => p.bot === id).at(-1);
  const [err, setErr] = useState<string | null>(null);
  const live = b.environment === "live";
  const tone = b.stopped_for_day ? "card-daystop" : inTrade ? "card-trade" : "";
  const tf = (b.timeframes ?? [b.timeframe]).join(" · ");
  const href = `/bot?id=${id}`;
  const tip = `${id} · ${b.name}. ${CHARACTERS[id]?.personality ?? ""}`;
  async function more(action: "stop" | "retire", close: () => void) {
    close(); setErr(null);
    try { await botAction(id, action, b.open_positions.length); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  }
  return (
    <article className={`botcard bc-${b.side} ${tone} ${live ? "env-live" : ""}`} data-bot={id} data-status={b.status} data-open={b.open_positions.length}
             aria-label={`${name}, ${b.side === "L" ? "long" : "short"} bot`}>
      <Link href={href} className="botcard-art" title={tip} aria-label={`Open ${name}`} tabIndex={-1}>
        <Creature bot={id} size={150} mood={cardMood(b, kill)} scene react={react} />
        <StateSymbol state={stateSymbol(b.status, kill)} size={28} />
        <SideIcon bot={id} size={40} />
        {live && <span className="plaque plaque-live">LIVE</span>}
        {inTrade ? <span className="plaque plaque-open" title="Open profit or loss of its trade(s) right now">Open <Pnl v={b.unrealized} /></span>
          : <span className="plaque" title="Today's profit or loss"><Pnl v={b.today_pnl} /></span>}
        {coin && <span key={coin.id} className="coin"><Pnl v={coin.net} /></span>}
      </Link>
      <Link href={href} className="botcard-name" title={tip}><b>{name}</b><span className="tf">{tf}</span></Link>
      {inTrade && <p className="in-trade">{b.open_positions.map((p: Any) =>
        `${p.side === "LONG" ? "▲" : "▼"} ${p.volume_open} ${p.symbol} from ${Number(p.entry_price).toFixed(String(p.symbol).startsWith("XAG") ? 3 : 2)}`).join(" · ")}</p>}
      <p className="thought">{b.thought}</p>
      {b.stopped_for_day && <div className="daystop">Done for today: {b.stopped_for_day}</div>}
      {Object.keys(b.readiness ?? {}).length > 0 && b.status !== "RETIRED" && (
        <div className="readiness">
          {Object.entries(b.readiness).map(([role, r]: [string, Any]) => (
            <Meter key={role} label={role === "GOLD" ? "Gold" : "Silver"} met={r.met} total={r.total}
                   title={`${role === "GOLD" ? "Gold" : "Silver"}: ${r.met} of ${r.total} conditions for a ${b.side === "L" ? "long" : "short"} met`} />
          ))}
        </div>
      )}
      <div className="card-stats">
        <div><span>Equity</span><b>{money(b.equity)}</b></div>
        <div><span>Today</span><b><Pnl v={b.today_pnl} /></b></div>
        <div><span>Trades</span><b>{b.entries_today}{b.max_entries_per_day ? ` / ${b.max_entries_per_day}` : ""}</b></div>
      </div>
      <div className="card-actions">
        <StateToggle bot={id} status={b.status} openTrades={b.open_positions.length} kill={kill} />
        <Menu label={`More for ${name}`}>{(close) => <>
          <Link href={href} role="menuitem" onClick={close}>Open {name}</Link>
          <Link href={`/trades?bot=${id}`} role="menuitem" onClick={close}>Its trades</Link>
          {b.status !== "STOPPED" && <Button role="menuitem" disabled={inTrade} title={inTrade ? "Close its trades first, or pause it" : ""} onClick={() => more("stop", close)}>Stop</Button>}
          <Button role="menuitem" disabled={inTrade} title={inTrade ? "Close its trades first" : ""} onClick={() => more("retire", close)}>Retire</Button>
        </>}</Menu>
      </div>
      {err && <span className="card-err">{err}</span>}
    </article>
  );
}
