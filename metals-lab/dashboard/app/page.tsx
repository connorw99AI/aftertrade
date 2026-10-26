"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { botAction, StateToggle } from "@/components/Bits";
import { BotCard } from "@/components/BotCard";
import { Creature, CREATURES, StateSymbol } from "@/components/Creature";
import { SessionRibbon } from "@/components/SessionRibbon";
import { ActionNow } from "@/components/ActionNow";
import { ArmControl } from "@/components/ArmControl";
import { useSummary } from "@/components/Shell";
import { Button } from "@/components/ui";
import { Any, api, confirmAction } from "@/lib/api";

/** A retired bot collapses to one line: who it is, why it was retired, and a way back. */
function RetiredRow({ b }: { b: Any }) {
  return (
    <div className={`retired-row bc-${b.side}`}>
      <Link href={`/bot?id=${b.bot_id}`} className="who"><Creature bot={b.bot_id} size={34} mood="asleep" />
        <span><b>{CREATURES[b.bot_id]?.name}</b> <span className="muted tiny">{b.bot_id}</span></span></Link>
      <span className="muted small retired-why">{b.pause_reason || "retired"}</span>
      <div className="actions"><StateToggle bot={b.bot_id} status="RETIRED" openTrades={0} /></div>
    </div>
  );
}

function BulkControls({ sum }: { sum: Any }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const kill = !!sum.kill;
  // bots in a trade first (largest open profit or loss first), then the rest in their usual order
  const active = sum.bots.filter((b: Any) => b.status !== "RETIRED")
    .map((b: Any, i: number) => ({ b, i })).sort((x: Any, y: Any) =>
      Number(y.b.open_positions.length > 0) - Number(x.b.open_positions.length > 0)
      || (y.b.open_positions.length > 0 ? Math.abs(y.b.unrealized ?? 0) - Math.abs(x.b.unrealized ?? 0) : 0) || x.i - y.i)
    .map((x: Any) => x.b);
  const running = active.filter((b: Any) => b.status === "RUNNING").length;
  const trades = sum.open_trades ?? 0;
  const inTrade = active.filter((b: Any) => b.open_positions.length).length;
  async function run(action: "start" | "pause" | "kill") {
    const req = {
      start: { title: "Start every bot?", tone: "go" as const, confirmLabel: "Start all",
        consequences: [`${active.length - running} bot${active.length - running === 1 ? "" : "s"} that are not running start checking for entries.`, "Retired bots stay retired."] },
      pause: { title: "Pause every running bot?", tone: "pause" as const, confirmLabel: "Pause all",
        consequences: [`${running} running bot${running === 1 ? "" : "s"} stop opening new trades.`, trades ? `${trades} open trade${trades === 1 ? " keeps its" : "s keep their"} stops, targets and exits.` : "No trades are open."] },
      kill: { title: "Kill switch: stop everything?", tone: "danger" as const, confirmLabel: "Kill all", typed: "KILL",
        consequences: [trades ? `Closes ${trades} open trade${trades === 1 ? "" : "s"} on ${inTrade} bot${inTrade === 1 ? "" : "s"} at market now.` : "No trades are open to close.",
          "Disarms the lab and blocks every new trade.", "Only clears from the command line: metals-lab clear-kill."] },
    }[action];
    const name = await confirmAction(req);
    if (!name) return;
    setBusy(true); setErr(null);
    try {
      if (action === "kill") await api.post("/api/risk/kill", { operator: name, reason: "Kill all from the Lab" });
      else await api.post(`/api/bots-all/${action}`, { operator: name, reason: `${action} all from the Lab` });
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return (
    <div className="bulk">
      <Button tone="go" disabled={busy || kill} title={kill ? "Clear the kill switch first" : ""} onClick={() => run("start")}><StateSymbol state="run" size={20} /> Start all</Button>
      <Button tone="pause" disabled={busy} onClick={() => run("pause")}><StateSymbol state="pause" size={20} /> Pause all</Button>
      <Button tone="danger" disabled={busy || kill} onClick={() => run("kill")}><StateSymbol state="kill" size={20} /> Kill all</Button>
      {err && <span className="err">{err}</span>}
    </div>
  );
}

/** Lab zoom: smaller cards fit more bots per row. Remembered per browser; phones start small. */
const ZOOMS = [1, 0.85, 0.7, 0.55, 0.45];
const ZOOM_KEY = "metal-lab-overview-zoom";

function ZoomControl({ zoom, setZoom }: { zoom: number; setZoom: (z: number) => void }) {
  const i = ZOOMS.indexOf(zoom);
  return (
    <div className="zoom" role="group" aria-label="Card size">
      <button onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, i + 1)])} disabled={i === ZOOMS.length - 1} aria-label="Zoom out: smaller cards, more per row" title="Zoom out">−</button>
      <button className="zoom-level" onClick={() => setZoom(1)} title="Reset to 100%">{Math.round(zoom * 100)}%</button>
      <button onClick={() => setZoom(ZOOMS[Math.max(0, i - 1)])} disabled={i === 0} aria-label="Zoom in: bigger cards" title="Zoom in">+</button>
    </div>
  );
}

/** S and P start or pause the bot whose card has keyboard focus. */
function useCardShortcuts() {
  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [role=dialog], [role=alertdialog]") || e.metaKey || e.ctrlKey || e.altKey) return;
      const card = t.closest<HTMLElement>("article[data-bot]");
      const key = e.key.toLowerCase();
      if (!card || (key !== "s" && key !== "p")) return;
      const status = card.dataset.status;
      if (key === "s" && status !== "RUNNING" && status !== "RETIRED") { e.preventDefault(); await botAction(card.dataset.bot!, "start").catch(() => {}); }
      if (key === "p" && status === "RUNNING") { e.preventDefault(); await botAction(card.dataset.bot!, "pause", Number(card.dataset.open)).catch(() => {}); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

export default function Lab() {
  const sum = useSummary();
  const [busy, setBusy] = useState(false);
  const [zoom, setZoomState] = useState(1);
  useCardShortcuts();
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(ZOOM_KEY);
      const z = Number(stored);
      if (stored && ZOOMS.includes(z)) setZoomState(z);
    } catch { /* not stored */ }
  }, []);
  // phones get a compact two-column roster at full text size instead of a shrunken desktop card
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    const q = window.matchMedia("(max-width: 640px)");
    const f = () => setPhone(q.matches);
    f(); q.addEventListener("change", f);
    return () => q.removeEventListener("change", f);
  }, []);
  function setZoom(z: number) {
    setZoomState(z);
    try { window.localStorage.setItem(ZOOM_KEY, String(z)); } catch { /* not stored */ }
  }
  if (!sum) return <p className="empty">Loading…</p>;
  const shadow = sum.bots.some((b: Any) => b.shadow);
  // bots in a trade first (largest open profit or loss first), then the rest in their usual order
  const active = sum.bots.filter((b: Any) => b.status !== "RETIRED")
    .map((b: Any, i: number) => ({ b, i })).sort((x: Any, y: Any) =>
      Number(y.b.open_positions.length > 0) - Number(x.b.open_positions.length > 0)
      || (y.b.open_positions.length > 0 ? Math.abs(y.b.unrealized ?? 0) - Math.abs(x.b.unrealized ?? 0) : 0) || x.i - y.i)
    .map((x: Any) => x.b);
  const retired = sum.bots.filter((b: Any) => b.status === "RETIRED");
  async function unretireAll() {
    const name = await confirmAction({ title: `Un-retire all ${retired.length}?`, tone: "go", confirmLabel: "Un-retire all",
      consequences: ["Each comes back stopped, with its own balance.", "Press Start when you want them trading."] });
    if (!name) return;
    setBusy(true);
    try { await api.post("/api/bots-all/unretire", { operator: name, reason: "un-retire all from the Lab" }); }
    finally { setBusy(false); }
  }
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Lab</h1>
          <p className="lede">Every bot on the bench. Click a creature for its full view. <span className="tiny">Keys: <kbd>/</kbd> jump to a bot, <kbd>S</kbd> / <kbd>P</kbd> start or pause the focused card.</span>
            {shadow ? " Shadow mode: amounts are what each bot would get once you arm." : ""}</p>
        </div>
        <div className="lab-tools"><ZoomControl zoom={zoom} setZoom={setZoom} /><BulkControls sum={sum} /></div>
      </div>
      {!sum.armed && !sum.kill && <div className="notice row-between"><span>Not armed: bots only watch and record what they would do.
        {shadow ? " Amounts shown are what each bot would get once you arm." : ""}</span><ArmControl armed={false} /></div>}
      {sum.kill && <div className="notice notice-danger">Kill switch is on: every bot is blocked. Clear it with <code>metals-lab clear-kill</code>, then arm and start again.</div>}
      <ActionNow bots={sum.bots} />
      <SessionRibbon bots={sum.bots} serverTime={sum.server_time} storeKey="metal-lab-ribbon-lab" />
      <div style={{ height: 16 }} />
      <div className={`botgrid ${zoom <= 0.7 || phone ? "compact" : ""}`} style={phone ? undefined : { zoom }}>
        {active.map((b: Any) => <BotCard key={b.bot_id} b={b} kill={!!sum.kill} />)}
      </div>
      {retired.length > 0 && (
        <section className="retired-list">
          <div className="section-head"><h2>Retired ({retired.length})</h2><Button disabled={busy} onClick={unretireAll}>Un-retire all</Button></div>
          {retired.map((b: Any) => <RetiredRow key={b.bot_id} b={b} />)}
        </section>
      )}
    </>
  );
}
