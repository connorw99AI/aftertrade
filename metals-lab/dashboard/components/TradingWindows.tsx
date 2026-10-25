"use client";
import { useEffect, useState } from "react";
import { CREATURES } from "@/components/Creature";
import { Button } from "@/components/ui";
import { Any, api, confirmAction } from "@/lib/api";

const DAY = 24 * 60;
const ALL_DAY = "00:00-24:00";
const METALS = [["gold", "Gold"], ["silver", "Silver"]] as const;
const KEYS = ["trade_gold", "trade_silver", "gold_windows", "silver_windows"];
const WINDOW = /^([01]?\d|2[0-4]):[0-5]\d-([01]?\d|2[0-4]):[0-5]\d$/;

const split = (spec: string) => spec.split(",").map((x) => x.trim()).filter(Boolean);
const minute = (t: string) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
/** "22:00-02:00" -> [[1320, 1440], [0, 120]]: a window across midnight is drawn in two parts. */
function bars(spec: string): number[][] {
  return split(spec).flatMap((w) => {
    const [a, b] = w.split("-").map(minute);
    return a < b ? [[a, b]] : [[a, DAY], [0, b]];
  });
}

type Vals = Record<string, string>;
const pick = (params: Record<string, Any>): Vals => Object.fromEntries(KEYS.map((k) => [k, String(params[k]?.value ?? "")]));

/** Per-metal switches and permitted trading windows (UK time) for one bot, with "copy to all bots". */
export function TradingWindows({ bot, onDone }: { bot: Any; onDone: () => void }) {
  const params = bot.strategy.params as Record<string, Any>;
  const pair = !!bot.strategy.pair_trades;
  const [vals, setVals] = useState<Vals>(pick(params));
  const [adding, setAdding] = useState<Record<string, string>>({ gold: "", silver: "" });
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => setVals(pick(params)), [bot.config_version]); // eslint-disable-line react-hooks/exhaustive-deps
  const locked = bot.status === "RUNNING" || bot.open_positions.length > 0;
  const dirty = KEYS.some((k) => vals[k] !== String(params[k]?.value ?? ""));

  function setWindows(metal: string, list: string[]) { setVals({ ...vals, [`${metal}_windows`]: list.length ? list.join(", ") : ALL_DAY }); }
  function add(metal: string) {
    const w = adding[metal].replace(/\s+/g, "");
    if (!WINDOW.test(w)) { setMsg("Write a window as HH:MM-HH:MM, e.g. 06:00-11:00 (22:00-02:00 crosses midnight)."); return; }
    const cur = split(vals[`${metal}_windows`]).filter((x) => x !== ALL_DAY);
    setWindows(metal, [...cur, w]);
    setAdding({ ...adding, [metal]: "" });
    setMsg(null);
  }
  const full = (v: Vals) => ({ ...Object.fromEntries(Object.entries(params).map(([k, p]) => [k, p.value])), ...v });

  async function save() {
    const name = await confirmAction({ title: `Save trading windows for ${CREATURES[bot.bot_id]?.name}?`, confirmLabel: "Save",
      consequences: [...METALS.map(([m, M]) => `${M}: ${vals[`trade_${m}`] === "off" && !pair ? "off" : `${vals[`${m}_windows`]} UK`}`),
        `Saved as settings v${(bot.config_version ?? 1) + 1}; the change is logged.`] });
    if (!name) return;
    try { await api.put(`/api/bots/${bot.bot_id}/params`, { params: full(vals), operator: name }); setMsg("Saved."); onDone(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  async function copyToAll() {
    const others = Object.keys(CREATURES).filter((id) => id !== bot.bot_id);
    const name = await confirmAction({ title: "Copy these metals and windows to every bot?", confirmLabel: "Copy",
      consequences: [...METALS.map(([m, M]) => `${M}: ${vals[`trade_${m}`] === "off" ? "off" : `${vals[`${m}_windows`]} UK`}`),
        "Bots that are running or have open trades are skipped; pause them first.", "Each bot's change is logged."] });
    if (!name) return;
    const done: string[] = [], skipped: string[] = [];
    for (const id of others) {
      try {
        const b = await api.get<Any>(`/api/bots/${id}`);
        if (b.status === "RUNNING" || b.open_positions.length) { skipped.push(CREATURES[id]?.name ?? id); continue; }
        const p = Object.fromEntries(Object.entries(b.strategy.params as Record<string, Any>).map(([k, x]) => [k, x.value]));
        await api.put(`/api/bots/${id}/params`, { params: { ...p, ...vals }, operator: name });
        done.push(id);
      } catch { skipped.push(CREATURES[id]?.name ?? id); }
    }
    setMsg(`Copied to ${done.length} bots.${skipped.length ? ` Skipped (running, open trades or error): ${skipped.join(", ")}.` : ""}`);
  }

  return (
    <div className="windows-editor">
      {locked && <div className="notice">Pause the bot and close its trades to change its metals or windows.</div>}
      {pair && <p className="muted small">This bot trades gold and silver together, so a pair opens only when both metals&apos; windows are open.</p>}
      {METALS.map(([m, M]) => {
        const on = pair || vals[`trade_${m}`] !== "off";
        const list = split(vals[`${m}_windows`]);
        return (
          <div key={m} className={`metal-row ${on ? "" : "off"}`}>
            <div className="row-between">
              <b>{M}</b>
              {!pair && <label className="switch"><input type="checkbox" checked={on} disabled={locked}
                onChange={(e) => setVals({ ...vals, [`trade_${m}`]: e.target.checked ? "on" : "off" })} /> {on ? "Trades" : "Off"}</label>}
            </div>
            <div className="day-strip" aria-label={`${M} trading windows`}>
              {bars(vals[`${m}_windows`]).map(([a, z]) => <i key={`${a}-${z}`} style={{ left: `${(a / DAY) * 100}%`, width: `${((z - a) / DAY) * 100}%` }} />)}
            </div>
            <div className="strip-axis tiny muted">{[0, 6, 12, 18, 24].map((h) => <span key={h}>{String(h).padStart(2, "0")}</span>)}</div>
            <div className="chips">
              {list.map((w) => (
                <span key={w} className="chip">{w === ALL_DAY ? "All day" : w}
                  {!locked && w !== ALL_DAY && <button type="button" aria-label={`Remove ${w}`} onClick={() => setWindows(m, list.filter((x) => x !== w))}>×</button>}
                </span>))}
              {!locked && <>
                <input aria-label={`Add a ${m} window`} placeholder="06:00-11:00" size={11} value={adding[m]}
                  onChange={(e) => setAdding({ ...adding, [m]: e.target.value })} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(m); } }} />
                <Button size="sm" type="button" onClick={() => add(m)}>Add</Button>
                {vals[`${m}_windows`] !== ALL_DAY && <Button size="sm" type="button" tone="ghost" onClick={() => setWindows(m, [])}>All day</Button>}
              </>}
            </div>
          </div>
        );
      })}
      <p className="tiny muted">UK time (follows BST/GMT). 00:00-24:00 is all day; hours the market is shut are blocked anyway. A window like 22:00-02:00 crosses midnight.</p>
      <div className="actions">
        <Button tone="primary" type="button" disabled={locked || !dirty} onClick={save}>Save windows</Button>
        <Button type="button" tone="ghost" onClick={copyToAll} disabled={dirty} title={dirty ? "Save first" : ""}>Copy to all bots</Button>
        {msg && <span className="muted">{msg}</span>}
      </div>
    </div>
  );
}
