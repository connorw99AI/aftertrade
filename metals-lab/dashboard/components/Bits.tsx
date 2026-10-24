"use client";
import { useState } from "react";
import { Any, api, confirmAction } from "@/lib/api";
import { CREATURES, StateSymbol } from "./Creature";
import { Button, Pill } from "./ui";
import { PillTone } from "./ui/Pill";

export const STATUS_TEXT: Record<string, string> = {
  RUNNING: "Running", PAUSED: "Paused", PAUSED_RISK: "Paused (drawdown)", STOPPED: "Stopped", RETIRED: "Retired",
};
const STATUS_TONE: Record<string, PillTone> = { RUNNING: "run", PAUSED: "pause", PAUSED_RISK: "pause", STOPPED: "stop", RETIRED: "retired" };

export function StatusBadge({ status, extra }: { status: string; extra?: string | null }) {
  return <Pill tone={STATUS_TONE[status] ?? "muted"}>{STATUS_TEXT[status] ?? status}{extra ? ` · ${extra}` : ""}</Pill>;
}

export const VERDICT: Record<string, { text: string; tone: string }> = {
  ENTRY_LONG: { text: "Entry long", tone: "v-entry" },
  ENTRY_SHORT: { text: "Entry short", tone: "v-entry" },
  NO_ENTRY: { text: "No entry", tone: "v-none" },
  IN_TRADE_LONG: { text: "In long", tone: "v-trade" },
  IN_TRADE_SHORT: { text: "In short", tone: "v-trade" },
  WAITING_DATA: { text: "Waiting for data", tone: "v-wait" },
};

export function VerdictPill({ verdict }: { verdict?: string }) {
  const v = VERDICT[verdict ?? ""] ?? { text: verdict ? verdict.toLowerCase() : "–", tone: "v-wait" };
  return <span className={`verdict ${v.tone}`}>{v.text}</span>;
}

/** How close a bot is to an entry on one metal: its filters plus its own side's conditions ("trades allowed" lines carry no information). */
export function closeness(r: Any, side?: "L" | "S"): { score: number; met: number; total: number } {
  if (!r || "waiting" in r || !r.filters) return { score: 0, met: 0, total: 0 };
  const f = (r.filters as Any[]).filter((c) => !String(c.label).toLowerCase().startsWith("metal allowed"));
  const sides = side ? [side === "L" ? "long" : "short"] : ["long", "short"];
  let best = { score: 0, met: 0, total: 0 };
  for (const k of sides) {
    const own = ((r[k] ?? []) as Any[]).filter((c) => !String(c.label).toLowerCase().includes("trades allowed"));
    const total = f.length + own.length;
    const met = [...f, ...own].filter((c) => c.ok).length;
    const score = total ? met / total : 0;
    if (score >= best.score) best = { score, met, total };
  }
  return best;
}

export type BotAction = "start" | "pause" | "stop" | "retire" | "unretire";

/** Ask, then change one bot's state. Every question says what happens to its trades. Returns true when done. */
export async function botAction(bot: string, action: BotAction, openTrades = 0): Promise<boolean> {
  const who = CREATURES[bot]?.name ?? bot;
  const req = {
    start: { title: `Start ${who}?`, tone: "go" as const, confirmLabel: "Start",
      consequences: ["It begins checking for entries on its next completed bar.", "Orders are only sent while the lab is armed on the demo account."] },
    pause: { title: `Pause ${who}?`, tone: "pause" as const, confirmLabel: "Pause",
      consequences: ["No new entries until you start it again.", openTrades ? `Its ${openTrades} open trade${openTrades > 1 ? "s keep their" : " keeps its"} stop, target and exit rules.` : "It has no open trades."] },
    stop: { title: `Stop ${who}?`, tone: "danger" as const, confirmLabel: "Stop",
      consequences: ["It stops watching the market entirely.", "Start it again at any time; its balance stays."] },
    retire: { title: `Retire ${who}?`, tone: "danger" as const, confirmLabel: "Retire",
      consequences: ["It stops opening trades and its card collapses to one line.", "It keeps its own balance; un-retire brings it back stopped."] },
    unretire: { title: `Un-retire ${who}?`, tone: "go" as const, confirmLabel: "Un-retire",
      consequences: ["It comes back stopped, with its own balance.", "Press Start when you want it trading."] },
  }[action];
  const name = await confirmAction(req);
  if (!name) return false;
  await api.post(`/api/bots/${bot}/${action}`, { operator: name, reason: `${action} from dashboard` });
  return true;
}

/** The single state toggle: Pause when running, Start otherwise (Un-retire when retired). */
export function StateToggle({ bot, status, openTrades, onDone, kill }: { bot: string; status: string; openTrades: number; onDone?: () => void; kill?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const action: BotAction = status === "RETIRED" ? "unretire" : status === "RUNNING" ? "pause" : "start";
  async function go() {
    setBusy(true); setErr(null);
    try { if (await botAction(bot, action, openTrades)) onDone?.(); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    finally { setBusy(false); }
  }
  const label = { start: "Start", pause: "Pause", unretire: "Un-retire" }[action as "start" | "pause" | "unretire"];
  return (
    <>
      <Button tone={action === "pause" ? "pause" : "go"} disabled={busy || (action === "start" && kill)} title={action === "start" && kill ? "Clear the kill switch first" : ""} onClick={go}>
        <StateSymbol state={action === "pause" ? "pause" : "run"} size={20} /> {label}
      </Button>
      {err && <span className="card-err">{err}</span>}
    </>
  );
}

/** Start / Pause / Stop / Retire for the bot page, only the ones that change the current state. */
export function BotControls({ bot, status, hasOpen, openTrades = 0, onDone, retire = false, kill = false }: {
  bot: string; status: string; hasOpen: boolean; openTrades?: number; onDone?: () => void; retire?: boolean; kill?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function act(action: BotAction) {
    setBusy(true); setErr(null);
    try { if (await botAction(bot, action, openTrades)) onDone?.(); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    finally { setBusy(false); }
  }
  return (
    <div className="actions">
      {status === "RETIRED" ? (retire && <Button tone="go" disabled={busy} onClick={() => act("unretire")}>Un-retire</Button>) : <>
        {status !== "RUNNING" && <Button tone="go" disabled={busy || kill} onClick={() => act("start")}><StateSymbol state="run" size={20} /> Start</Button>}
        {status === "RUNNING" && <Button tone="pause" disabled={busy} onClick={() => act("pause")}><StateSymbol state="pause" size={20} /> Pause</Button>}
        {status !== "STOPPED" && <Button tone="stop" disabled={busy || hasOpen} title={hasOpen ? "Close its trades first, or pause it" : ""} onClick={() => act("stop")}>Stop</Button>}
        {retire && <Button tone="ghost" disabled={busy || hasOpen} title={hasOpen ? "Close its trades first" : ""} onClick={() => act("retire")}>Retire</Button>}
      </>}
      {err && <span className="err">{err}</span>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}
