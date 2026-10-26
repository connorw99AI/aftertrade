"use client";
import { useState } from "react";
import { BOTS, CREATURES } from "@/components/Creature";
import { Button, EmptyState, Panel, Stat, Stats, Table } from "@/components/ui";
import { Any, api, confirmAction, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const pc = (v: number) => `${+(v * 100).toFixed(2)}%`;
const metals = (v: Any, unit: string) =>
  v && typeof v === "object" ? Object.entries(v).map(([k, x]) => `${k === "GOLD" ? "gold" : "silver"} ${x}${unit}`).join(", ") : `${v}${unit}`;

/** Each risk limit as a sentence with its unit. Unknown keys still show, in words. */
function limitSentences(l: Any): [string, string][] {
  const out: [string, string][] = [];
  const say = (k: string, s: string | null) => { if (k in l && s) out.push([k, s]); };
  const caps = l.pre_trade_limits !== false;
  const off = caps ? "" : " (off: pre-trade caps are switched off)";
  say("risk_per_trade", `Each trade is sized to risk ${pc(l.risk_per_trade)} of the bot's balance at its stop${l.max_risk_per_trade_cap ? ` (no bot above ${pc(l.max_risk_per_trade_cap)})` : ""}, but never below the minimum size.`);
  if (l.big_lot && l.big_lot_max_risk) {
    say("min_lot", `Trade size: ${metals(l.big_lot, " lots")} when the strategy's stop is tight enough that this loses at most ${pc(l.big_lot_max_risk)} of the bot; otherwise ${metals(l.min_lot, " lots")}. The stop is always the strategy's own.`);
  } else say("min_lot", `Minimum trade size: ${metals(l.min_lot, " lots")}.`);
  say("per_trade_limit_enabled", l.per_trade_limit_enabled && caps ? "A trade whose stop would lose more than the per-trade risk is refused." : "A trade may risk more than the per-trade figure when the minimum size needs it.");
  say("pre_trade_limits", caps ? "Margin and exposure caps are checked before every entry." : "Margin-share and exposure caps are off; losses are handled by the daily stops and the drawdown pause.");
  say("max_entries_per_bot_per_day", `Each bot opens at most ${l.max_entries_per_bot_per_day} trades per UTC day (a two-leg pair counts once).`);
  say("max_trade_risk", l.max_trade_risk ? `A trade is skipped if even the minimum size would risk more than ${pc(l.max_trade_risk)} of the bot's balance.` : "There is no cap on what the minimum size may risk.");
  say("daily_loss_pause", `A bot opens nothing more that UTC day once it is down ${pc(l.daily_loss_pause)} on the day.`);
  say("daily_giveback", l.daily_giveback ? `Profit lock: once a bot is up ${pc(l.daily_lock_from ?? 0)} or more on the day, it stops for the day if it gives back ${pc(l.daily_giveback)} of its best gain that day.` : "There is no profit lock.");
  say("daily_profit_stop", l.daily_profit_stop ? `A bot opens nothing more that day once it is up ${pc(l.daily_profit_stop)}; open trades stay managed.` : "There is no daily profit stop.");
  say("weekly_loss_pause", l.weekly_loss_pause ? `A bot opens nothing more that UTC week once it is down ${pc(l.weekly_loss_pause)} on the week.` : "There is no weekly loss stop.");
  say("max_consecutive_losses", l.max_consecutive_losses ? `A bot pauses after ${l.max_consecutive_losses} losing trades in a row; you restart it with Start.` : "No pause after a losing streak.");
  say("cooldown_minutes_after_loss", l.cooldown_minutes_after_loss ? `After a losing trade a bot waits ${l.cooldown_minutes_after_loss} minutes before its next entry.` : "No cooldown after a loss.");
  say("max_lots_per_trade", `One trade is never larger than ${metals(l.max_lots_per_trade, " lots")}; risk sizing is clamped to it.`);
  say("max_lots_per_bot", l.max_lots_per_bot ? `All of one bot's open trades together stay at or below ${l.max_lots_per_bot} lots.` : "No cap on a bot's total open lots.");
  say("daily_headroom_check", l.daily_headroom_check ? "A trade is refused if a stop-out would take the bot past its daily loss stop." : "A trade may be taken even if a stop-out would take the bot past its daily loss stop.");
  say("close_on_breach", l.close_on_breach ? "When a bot hits its daily or weekly loss stop, drawdown pause or losing-streak pause, its own open trades are closed." : "Open trades keep running when a loss limit trips.");
  say("max_drawdown_pause", l.max_drawdown_pause ? `A bot pauses after falling ${pc(l.max_drawdown_pause)} from its peak; you restart it with Start.` : "There is no drawdown pause.");
  say("weekend_flat_from", l.weekend_flat_from ? `Every trade closes at ${l.weekend_flat_from} UTC on Friday; no entries until Sunday 22:00 UTC.` : "Trades are held over the weekend.");
  say("max_positions_per_bot", `Each bot holds at most ${l.max_positions_per_bot} trades at once.`);
  say("max_bot_margin_fraction", `A bot may tie up at most ${pc(l.max_bot_margin_fraction)} of its balance as margin${off}.`);
  say("min_account_margin_level", `No new trades while the account margin level is below ${l.min_account_margin_level}%.`);
  say("max_account_margin_used", `All bots together may use at most ${pc(l.max_account_margin_used)} of account equity as margin${off}.`);
  say("exposure_scope", l.exposure_scope === "per_bot" ? "Exposure caps count each bot's own trades against its own balance." : "Exposure caps count every bot's trades against the account's equity.");
  say("max_metals_gross_exposure", `Gold plus silver open at once: at most ${l.max_metals_gross_exposure} times the balance in notional value${off}.`);
  say("max_same_direction_exposure", `Open in one direction across both metals: at most ${l.max_same_direction_exposure} times the balance${off}.`);
  say("max_spread_points", `No entry when the spread is wider than ${metals(l.max_spread_points, " points")}.`);
  say("stop_buffer_points", `Stops sit at least ${l.stop_buffer_points} points beyond the broker's minimum distance.`);
  say("require_stop", l.require_stop ? "Every trade must carry a stop-loss." : "Trades may open without a stop-loss.");
  const known = new Set(out.map(([k]) => k).concat(["max_risk_per_trade_cap", "daily_lock_from", "big_lot", "big_lot_max_risk"]));
  for (const [k, v] of Object.entries(l)) if (!known.has(k)) out.push([k, `${k.replace(/_/g, " ")}: ${typeof v === "object" ? metals(v, "") : String(v)}.`]);
  return out;
}

const ACTION: Record<string, string> = {
  INITIAL_ALLOCATION: "Initial allocation", ALLOCATION_MOVE: "Allocation moved", STATUS: "Status changed", PARAMS: "Rules changed",
  ARM: "Armed", "ARM (dashboard)": "Armed from the dashboard", ALLOCATION_RESPLIT: "Starting share re-split for 22 bots", DISARM: "Disarmed", CLEAR_BACKTESTS: "Backtest results cleared", DELETE_HISTORY: "Price history deleted", KILL: "Kill switch", RETIRE: "Retired", UNRETIRE: "Un-retired",
};
const STATUS_WORD: Record<string, string> = { RUNNING: "Running", PAUSED: "Paused", PAUSED_RISK: "Paused (drawdown)", STOPPED: "Stopped", RETIRED: "Retired" };

function change(a: Any): string {
  const parse = (j: string | null) => { try { return j ? JSON.parse(j) : null; } catch { return j; } };
  const o = parse(a.old_json), n = parse(a.new_json);
  if (a.action === "INITIAL_ALLOCATION" || a.action === "ALLOCATION_MOVE") return n?.amount != null ? money(n.amount) : "";
  if (a.action === "ARM") return "demo account verified";
  if (a.action === "DELETE_HISTORY") return `${n?.deleted} download${n?.deleted === 1 ? "" : "s"}, ${((n?.bytes ?? 0) / 1e6).toFixed(1)} MB freed`;
  if (a.action === "CLEAR_BACKTESTS") return `${o?.backtests ?? 0} runs deleted`;
  if (n && typeof n === "object") {
    const keys = Object.keys(n).filter((k) => !o || typeof o !== "object" || JSON.stringify(o[k]) !== JSON.stringify(n[k]));
    return keys.slice(0, 4).map((k) => {
      const w = (v: Any) => (k === "status" ? STATUS_WORD[v] ?? v : typeof v === "object" ? JSON.stringify(v) : String(v));
      return o && typeof o === "object" && k in o ? `${k.replace(/_/g, " ")}: ${w(o[k])} → ${w(n[k])}` : `${k.replace(/_/g, " ")}: ${w(n[k])}`;
    }).join("; ") + (keys.length > 4 ? `; and ${keys.length - 4} more` : "");
  }
  return String(n ?? "");
}

/** Rows of the same action, operator and minute become one line ("Initial allocation: 22 bots, £227.27 each"). */
function groupAudit(rows: Any[]): Any[] {
  const out: Any[] = [];
  for (const a of rows) {
    const key = `${a.action}|${a.operator}|${String(a.created_at).slice(0, 16)}|${change(a)}`;
    const g = out.at(-1);
    if (g && g.key === key) { g.bots.push(a.bot_id); continue; }
    out.push({ ...a, key, bots: [a.bot_id] });
  }
  return out;
}

export default function Risk() {
  const { data: r, reload } = usePoll<Any>("/api/risk", 6000);
  const [note, setNote] = useState<string | null>(null);
  async function act(path: string, req: Parameters<typeof confirmAction>[0], body: Any = {}) {
    const name = await confirmAction(req);
    if (!name) return;
    try { await api.post(path, { ...body, operator: name }); setNote(null); reload(); }
    catch (e) { setNote(e instanceof Error ? e.message : String(e)); }
  }
  if (!r) return <p className="empty">Loading…</p>;
  const open = r.alerts.filter((a: Any) => !a.resolved_at);
  const audit = groupAudit(r.audit_log);
  return (
    <>
      <div className="grid2">
        <Panel title="Controls">
          <p className="muted">Pause stops new entries; open trades keep their server-side stops and strategy exits.</p>
          <div className="actions">
            <Button tone="pause" disabled={r.global_pause} onClick={() => act("/api/risk/pause-all", { title: "Pause every bot?", tone: "pause", confirmLabel: "Pause all",
              consequences: ["No bot opens a new trade until you resume.", "Open trades keep their stops, targets and exits."] }, { reason: "dashboard" })}>Pause all bots</Button>
            <Button tone="go" disabled={!r.global_pause} onClick={() => act("/api/risk/resume-all", { title: "Resume all bots?", tone: "go", confirmLabel: "Resume",
              consequences: ["Running bots may open trades again."] }, { reason: "dashboard" })}>Resume all bots</Button>
            <Button onClick={() => act("/api/disarm", { title: "Disarm?", confirmLabel: "Disarm",
              consequences: ["No orders are sent until you arm again (System > MT5 connection, or metals-lab arm).", "Bots keep thinking, in shadow mode."] }, { reason: "dashboard disarm" })}>Disarm</Button>
            <Button tone="danger" disabled={r.kill} onClick={() => act("/api/risk/kill", { title: "Kill switch: stop everything?", tone: "danger", confirmLabel: "Kill all", typed: "KILL",
              consequences: ["Closes every bot trade at market now.", "Disarms and blocks every new trade.", "Only clears from the command line: metals-lab clear-kill."] }, { reason: "dashboard kill" })}>Kill switch</Button>
          </div>
          <p className="muted small">Arm from System &gt; MT5 connection on the trading computer (demo accounts only, typed phrase) or with <code>metals-lab arm</code>. Kill switch: {r.kill_switch_behaviour}.</p>
          {note && <div className="notice notice-danger">{note}</div>}
        </Panel>
        <Panel title="Account and capital">
          {r.account ? <Stats>
            <Stat label="Balance" value={money(r.account.balance)} />
            <Stat label="Equity" value={money(r.account.equity)} />
            <Stat label="Margin used" value={money(r.account.margin)} />
            <Stat label="Margin level" value={r.account.margin_level ? `${r.account.margin_level.toFixed(0)}%` : "–"} />
            <Stat label="Starting snapshot" value={r.capital_snapshot ? money(r.capital_snapshot.starting_balance) : "not armed yet"} />
            <Stat label="Unallocated funds" value={money(r.unallocated_pool)} />
          </Stats> : <EmptyState>No terminal connected.</EmptyState>}
          {r.unallocated_events?.length > 0 && <p className="muted">{r.unallocated_events.length} balance operation(s) detected. They are never distributed automatically; use <code>metals-lab allocate</code>.</p>}
        </Panel>
      </div>
      <p className="muted small">{open.length ? `${open.length} open alert${open.length > 1 ? "s" : ""}: use the bell at the top right to read and resolve them.` : "No open alerts."}</p>
      <h2>Limits</h2>
      <ul className="stack" style={{ paddingLeft: 20, gap: 4 }}>{limitSentences(r.limits).map(([k, s]) => <li key={k} title={k}>{s}</li>)}</ul>
      <p className="muted small">Limits are planning numbers against each bot&apos;s own balance, not guarantees: gaps and slippage can make a loss larger than planned.</p>
      <h2>Audit log</h2>
      {audit.length === 0 ? <EmptyState>Nothing changed yet.</EmptyState> : (
        <Table><thead><tr><th>Time</th><th>What</th><th>Bots</th><th>By</th><th>Change</th></tr></thead>
          <tbody>{audit.map((a: Any) => {
            const bots = a.bots.filter(Boolean);
            const who = bots.length === 0 ? "system" : bots.length === BOTS.length ? `all ${bots.length} bots` : bots.length > 3 ? `${bots.length} bots` : bots.map((b: string) => CREATURES[b]?.name ?? b).join(", ");
            const c = change(a);
            return <tr key={a.id}><td>{when(a.created_at)}</td><td>{ACTION[a.action] ?? a.action.replace(/_/g, " ").toLowerCase()}</td><td>{who}</td><td>{a.operator}</td>
              <td className="muted">{c}{bots.length > 1 && c && (a.action === "INITIAL_ALLOCATION" || a.action === "ALLOCATION_MOVE") ? " each" : ""}</td></tr>;
          })}</tbody></Table>)}
    </>
  );
}
