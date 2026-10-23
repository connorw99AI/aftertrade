"use client";
import Link from "next/link";
import { useState } from "react";
import { Creature, CREATURES } from "@/components/Creature";
import { usePopups } from "@/components/Shell";
import { Any, api, money, pct } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

function moodOf(b: Any) {
  if (b.status === "PAUSED_RISK") return "hurt";
  if (b.status !== "RUNNING") return "asleep";
  return b.open_positions?.length ? "busy" : "idle";
}

function CardControls({ b, onDone }: { b: Any; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function act(action: "start" | "pause" | "stop") {
    setBusy(true);
    try { await api.post(`/api/bots/${b.bot_id}/${action}`, { reason: `overview ${action}` }); setErr(null); onDone(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const running = b.status === "RUNNING";
  return (
    <div className="card-controls">
      {running
        ? <button onClick={() => act("pause")} disabled={busy} aria-label={`Pause ${b.bot_id}`}>Pause</button>
        : <button className="primary" onClick={() => act("start")} disabled={busy} aria-label={`Start ${b.bot_id}`}>Start</button>}
      <button onClick={() => act("stop")} disabled={busy || b.status === "STOPPED"} aria-label={`Stop ${b.bot_id}`}>Stop</button>
      {err && <span className="card-err" role="alert">{err}</span>}
    </div>
  );
}

export default function Overview() {
  const { data: bots, error, reload } = usePoll<Any[]>("/api/bots", 5000);
  const { data: risk } = usePoll<Any>("/api/risk", 10000);
  const popups = usePopups();
  const snap = risk?.capital_snapshot;
  return (
    <>
      <div className="hero">
        <div>
          <h1>Ten bots, two metals, one demo account</h1>
          <p className="muted">
            Each creature trades its own virtual slice of the demo account.
            {snap ? ` Starting balance ${money(snap.starting_balance)} ${snap.currency ?? ""}, ${Math.round(snap.bot_fraction * 100)}% per bot.`
              : " Allocations are created when you arm the system for the first time."}
            {" "}Everything here is forward activity on the connected terminal; historical replays live under Backtest & replay.
          </p>
        </div>
      </div>
      {error && <div className="warn danger">Backend unreachable: {error}. Start it with scripts\windows\start.bat or metals-lab serve.</div>}
      {!bots && !error && <p className="empty">Waking the creatures…</p>}
      <section className="terrarium" aria-label="Bots">
        {bots?.map((b) => {
          const pop = popups.filter((p) => p.bot === b.bot_id).at(-1);
          const metal = b.roles.length > 1 ? "BOTH" : b.roles[0];
          return (
            <article key={b.bot_id} className="habitat">
              <Link href={`/bot?id=${b.bot_id}`} className="habitat-link" aria-label={`Open ${CREATURES[b.bot_id]?.name}`}>
                <div className="sky">
                  {pop && <span className={`coin ${pop.net >= 0 ? "gain" : "loss"}`}>{money(pop.net, true)}</span>}
                  {b.open_positions.length > 0 && <span className="pos-pip">{b.open_positions.length} open</span>}
                  <span className="tf-tag">{b.timeframe}</span>
                  <Creature bot={b.bot_id} size={96} mood={moodOf(b)} />
                </div>
                <div className={`ground ${metal}`} />
              </Link>
              <div className="info">
                <div className="row"><span className="who">{CREATURES[b.bot_id]?.name}</span><span className={`badge ${b.status}`}>{b.status.replace("_", " ").toLowerCase()}</span></div>
                <div className="what">{b.bot_id} {b.name}, {b.style.toLowerCase()} on {b.timeframe}</div>
                <div className="row"><span className="muted">Equity</span><span className="num">{b.book ? money(b.book.equity) : "not allocated"}{b.book?.shadow ? " (shadow)" : ""}</span></div>
                <div className="row"><span className="muted">Closed P&amp;L</span><span className={`num ${b.closed_net_pnl >= 0 ? "gain" : "loss"}`}>{money(b.closed_net_pnl, true)}</span></div>
                <div className="row"><span className="muted">Return</span><span className="num">{pct(b.return_pct)}</span></div>
                <div className="row"><span className="muted">Trades</span><span className="num">{b.closed_trades}{b.provisional_trades ? ` (${b.provisional_trades} provisional)` : ""}</span></div>
                <CardControls b={b} onDone={reload} />
              </div>
            </article>
          );
        })}
      </section>
    </>
  );
}
