"use client";
import Link from "next/link";
import { BotControls, StatusBadge, VerdictPill } from "@/components/Bits";
import { useEffect, useState } from "react";
import { Creature, CREATURES, Mood, SideIcon, StateSymbol, stateSymbol } from "@/components/Creature";
import { useSummary } from "@/components/Shell";
import { Any, api, confirmAction, money } from "@/lib/api";

function mood(b: Any, kill: boolean): Mood {
  if (b.status === "PAUSED_RISK" || b.stopped_for_day === "daily loss stop") return "hurt";
  if (kill || b.status === "STOPPED" || b.status === "PAUSED") return "still";
  if (b.open_positions.length) return "busy";
  if (b.today_pnl > 0) return "happy";
  return "idle";
}

function BotCard({ b, kill }: { b: Any; kill: boolean }) {
  const inTrade = b.open_positions.length > 0;
  // only the metals this bot may trade (a gold-only bot has nothing to say about silver), plus any metal it is in a trade on
  const roles = Object.keys(b.verdicts || {}).filter((r) => b.metals === "both" || r.toLowerCase() === b.metals
    || b.open_positions.some((p: Any) => p.role === r));
  const tone = b.stopped_for_day ? "card-stopped" : inTrade ? "card-trade" : "";
  return (
    <Link href={`/bot?id=${b.bot_id}`} className={`botcard bc-${b.side} ${tone}`}>
      <div className="botcard-top">
        <span className="botcard-tags"><span className="tf-tag">{b.timeframe}</span>{b.status !== "RUNNING" && <StatusBadge status={b.status} />}</span>
        <SideIcon bot={b.bot_id} />
      </div>
      <div className="botcard-art">
        <Creature bot={b.bot_id} size={150} mood={mood(b, kill)} scene />
        <StateSymbol state={stateSymbol(b.status, kill)} />
      </div>
      <div className="botcard-name">
        <b>{CREATURES[b.bot_id]?.name}</b>
        <span className="muted">{b.bot_id} · {b.name}</span>
      </div>
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

/** A retired half collapses to one line: who it is, why it was retired, and a way back. */
function RetiredRow({ b }: { b: Any }) {
  return (
    <div className={`retired-row bc-${b.side}`}>
      <Link href={`/bot?id=${b.bot_id}`} className="who"><Creature bot={b.bot_id} size={34} mood="asleep" />
        <span><b>{CREATURES[b.bot_id]?.name}</b> <span className="muted small">{b.bot_id} · {b.name}</span></span></Link>
      <span className="muted small retired-why">{b.pause_reason || "retired"}</span>
      <BotControls bot={b.bot_id} status={b.status} hasOpen={false} retire />
    </div>
  );
}

function BulkControls({ kill }: { kill: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run(action: "start" | "pause" | "kill") {
    const q = {
      start: "Start every bot that isn't retired?",
      pause: "Pause every running bot? Open trades keep their stops and exits.",
      kill: "Kill switch: close every bot trade now, disarm, and block all new trades until you clear it. Continue?",
    }[action];
    const name = confirmAction(q);
    if (!name) return;
    if (action === "kill" && (window.prompt('Type KILL to confirm') || "").trim().toUpperCase() !== "KILL") return;
    setBusy(true);
    setErr(null);
    try {
      if (action === "kill") await api.post("/api/risk/kill", { operator: name, reason: "Kill all from the Overview" });
      else await api.post(`/api/bots-all/${action}`, { operator: name, reason: `${action} all from the Overview` });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="bulk">
      <button className="btn-go" disabled={busy || kill} title={kill ? "Clear the kill switch first" : ""} onClick={() => run("start")}>
        <StateSymbol state="run" size={20} /> Start all</button>
      <button className="btn-pause" disabled={busy} onClick={() => run("pause")}><StateSymbol state="pause" size={20} /> Pause all</button>
      <button className="btn-kill" disabled={busy || kill} onClick={() => run("kill")}><StateSymbol state="kill" size={20} /> Kill all</button>
      {err && <span className="err">{err}</span>}
    </div>
  );
}

/** Overview zoom: smaller cards fit more bots per row. Remembered per browser. */
const ZOOMS = [1, 0.85, 0.7, 0.55, 0.45];
const ZOOM_KEY = "metal-lab-overview-zoom";

function ZoomControl({ zoom, setZoom }: { zoom: number; setZoom: (z: number) => void }) {
  const i = ZOOMS.indexOf(zoom);
  return (
    <div className="zoom" role="group" aria-label="Card size">
      <button onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, i + 1)])} disabled={i === ZOOMS.length - 1}
              aria-label="Zoom out: smaller cards, more per row" title="Zoom out">−</button>
      <button className="zoom-level" onClick={() => setZoom(1)} title="Reset to 100%">{Math.round(zoom * 100)}%</button>
      <button onClick={() => setZoom(ZOOMS[Math.max(0, i - 1)])} disabled={i === 0} aria-label="Zoom in: bigger cards" title="Zoom in">+</button>
    </div>
  );
}

export default function Overview() {
  const sum = useSummary();
  const [busy, setBusy] = useState(false);
  const [zoom, setZoomState] = useState(1);
  useEffect(() => {
    try { const z = Number(window.localStorage.getItem(ZOOM_KEY)); if (ZOOMS.includes(z)) setZoomState(z); } catch { /* not stored */ }
  }, []);
  function setZoom(z: number) {
    setZoomState(z);
    try { window.localStorage.setItem(ZOOM_KEY, String(z)); } catch { /* not stored */ }
  }
  if (!sum) return <p className="empty">Loading…</p>;
  const shadow = sum.bots.some((b: Any) => b.shadow);
  const active = sum.bots.filter((b: Any) => b.status !== "RETIRED");
  const retired = sum.bots.filter((b: Any) => b.status === "RETIRED");
  async function unretireAll() {
    const name = confirmAction(`Un-retire all ${retired.length} retired halves? Each comes back stopped with its own balance.`);
    if (!name) return;
    setBusy(true);
    try { await api.post("/api/bots-all/unretire", { operator: name, reason: "un-retire all from the Overview" }); }
    catch (e) { window.alert(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return (
    <>
      <div className="page-head row-between">
        <div>
          <h1>Overview</h1>
          <p className="muted">Click a creature for its full view: live checklist, trades, review and settings.
            {shadow ? " Shadow mode: amounts are what each bot would get once you arm." : ""}</p>
        </div>
        <div className="head-tools"><ZoomControl zoom={zoom} setZoom={setZoom} /><BulkControls kill={!!sum.kill} /></div>
      </div>
      {sum.kill && <div className="warn danger">Kill switch is on: every bot is blocked. Clear it with <code>metals-lab clear-kill</code>, then arm and start again.</div>}
      <div className={`botgrid ${zoom <= 0.7 ? "compact" : ""}`} style={{ zoom }}>
        {active.map((b: Any) => <BotCard key={b.bot_id} b={b} kill={!!sum.kill} />)}
      </div>
      {retired.length > 0 && (
        <section className="retired-list">
          <div className="row-between"><h2>Retired ({retired.length})</h2>
            <button disabled={busy} onClick={unretireAll}>Un-retire all</button></div>
          {retired.map((b: Any) => <RetiredRow key={b.bot_id} b={b} />)}
        </section>
      )}
    </>
  );
}
