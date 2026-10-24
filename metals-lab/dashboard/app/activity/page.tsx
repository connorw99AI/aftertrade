"use client";
import { useState } from "react";
import { BOTS, Creature, CREATURES } from "@/components/Creature";
import { Tabs } from "@/components/Bits";
import { Any, api, confirmAction, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const STAGE: Record<string, [string, string]> = {
  skipped_sizing: ["Skipped at sizing", "loss"], auditor_held: ["Held by auditor", "warnc"], risk_rejected: ["Rejected by risk", "loss"],
  approved: ["Approved", "gain"], other: ["", ""],
};

const REASON: Record<string, string> = {
  MIN_LOT_EXCEEDS_RISK_BUDGET: "minimum lot too big for the budget", MAX_ENTRIES_PER_DAY: "5 trades today already",
  DAILY_LOSS_LIMIT: "daily loss stop", DAILY_PROFIT_TARGET_REACHED: "daily profit stop", MAX_POSITIONS_PER_BOT: "2 trades already open",
  MAX_DRAWDOWN_LIMIT: "drawdown pause", INSUFFICIENT_BOT_FREE_CAPITAL: "not enough free money", ACCOUNT_MARGIN_LEVEL_LOW: "account margin level low",
  SPREAD_TOO_WIDE: "spread too wide", R04_SPREAD_EXPANSION: "spread spike", R03_STALE_QUOTE: "stale price", R01_SESSION_CLOSED: "market closed",
  R05_INSUFFICIENT_DATA: "not enough bars", R10_CORRELATED_SHOCK: "gold+silver shock", ORDER_IN_FLIGHT_FOR_SYMBOL: "order already in flight",
  BLOCKING_ALERTS: "an alert is blocking entries", RECONCILIATION_UNCERTAIN: "records not reconciled", GLOBAL_PAUSE: "all bots paused",
  KILL_SWITCH_ACTIVE: "kill switch", ACCOUNT_NOT_HEDGING: "account not hedging",
};
const reason = (k: string) => REASON[k] ?? k.replace(/^R\d+_/, "").replace(/_/g, " ").toLowerCase();

export default function Activity() {
  const [bot, setBot] = useState("");
  const [span, setSpan] = useState("today");
  const { data, reload } = usePoll<Any>(`/api/activity${bot ? `?bot=${bot}` : ""}`, 5000);
  const funnel: Any[] = (span === "today" ? data?.funnel : data?.funnel_7d) ?? [];
  const alerts: Any[] = data?.alerts ?? [];

  // identical alerts (same severity and message) are shown once with a count and resolved together
  const groups = Object.values(alerts.reduce((acc: Record<string, Any>, a) => {
    const k = `${a.severity}|${a.message}`;
    (acc[k] ??= { ...a, codes: [] as string[], first: a.ts, last: a.ts }).codes.push(a.code);
    if (a.ts < acc[k].first) acc[k].first = a.ts;
    if (a.ts > acc[k].last) acc[k].last = a.ts;
    return acc;
  }, {})) as Any[];

  async function resolve(g: Any) {
    const n = g.codes.length;
    const name = confirmAction(n > 1 ? `Mark all ${n} of these alerts as resolved?` : `Mark alert ${g.code} as resolved?`);
    if (!name) return;
    try {
      for (const code of g.codes) await api.post(`/api/alerts/${code}/resolve`, { operator: name, reason: "resolved from dashboard" });
    } catch (e) { window.alert(e instanceof Error ? e.message : String(e)); }
    reload();
  }

  return (
    <>
      <div className="page-head"><h1>Activity</h1>
        <p className="muted">Every decision a bot makes, and why trades did or did not happen.</p></div>
      {alerts.length > 0 && (
        <section className="panel alerts-panel">
          <h2>Open alerts</h2>
          <ul className="alert-list">
            {groups.map((g) => (
              <li key={g.id} className={`sev-${g.severity}`}>
                <b>{g.severity}</b>
                <span className="alert-msg">{g.message}{g.codes.length > 1 && <span className="alert-count">×{g.codes.length}</span>}</span>
                <span className="muted small">{g.codes.length > 1 ? `${when(g.first)} to ${when(g.last)}` : when(g.ts)}{g.blocks_entries ? ", blocks new entries" : ""}</span>
                <button className="small-btn" onClick={() => resolve(g)}>{g.codes.length > 1 ? `Resolve all ${g.codes.length}` : "Resolve"}</button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="row-between">
        <h2>Why trades did or did not happen</h2>
        <Tabs tabs={[["today", "Last 24 hours"], ["7d", "Last 7 days"]]} value={span} onChange={setSpan} />
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Bot</th><th className="n">Entry signals</th><th className="n">Skipped at sizing</th><th className="n">Held by auditor</th>
            <th className="n">Rejected by risk</th><th className="n">Approved</th><th className="n">Filled</th><th>Main reasons</th></tr></thead>
          <tbody>
            {funnel.map((f) => (
              <tr key={f.bot_id}>
                <td><span className="who"><Creature bot={f.bot_id} size={26} /> {CREATURES[f.bot_id]?.name}</span></td>
                <td className="n">{f.signals}</td><td className="n">{f.skipped_sizing || ""}</td><td className="n">{f.auditor_held || ""}</td>
                <td className="n">{f.risk_rejected || ""}</td><td className="n">{f.approved || ""}</td><td className="n"><b>{f.filled || ""}</b></td>
                <td className="small">{f.top_reasons.map(([k, n]: [string, number]) => `${reason(k)} (${n})`).join(", ") || (f.signals ? "" : <span className="muted">no entry signals: rules not met</span>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row-between">
        <h2>Decision feed</h2>
        <select value={bot} onChange={(e) => setBot(e.target.value)} aria-label="Filter by bot">
          <option value="">All bots</option>
          {BOTS.map((b) => <option key={b} value={b}>{CREATURES[b].name} ({b})</option>)}
        </select>
      </div>
      <ul className="feed">
        {(data?.feed ?? []).filter((e: Any) => e.kind !== "event" || e.event !== "TRADE_FINALIZED").map((e: Any, i: number) => e.kind === "decision" ? (
          <li key={i}>
            <span className="when">{when(e.ts)}</span>
            <span className="who"><Creature bot={e.bot_id} size={22} /> {CREATURES[e.bot_id]?.name}</span>
            <span><b>{(e.intent || "").replace("_", " ").toLowerCase()}</b> {e.role ? (e.role === "GOLD" ? "gold" : "silver") : ""}: {e.rationale}</span>
            <span className={`stage ${STAGE[e.stage]?.[1] ?? ""}`}>{e.outcome}{e.mode === "SHADOW" ? " (shadow)" : ""}</span>
          </li>
        ) : (
          <li key={i} className="ev">
            <span className="when">{when(e.ts)}</span>
            <span className="who">{e.bot_id ? <><Creature bot={e.bot_id} size={22} /> {CREATURES[e.bot_id]?.name}</> : "System"}</span>
            <span><b>{e.event.replace(/_/g, " ").toLowerCase()}</b> <span className="muted">{summarise(e.payload)}</span></span>
            <span />
          </li>
        ))}
        {data && data.feed.length === 0 && <li className="empty">Nothing yet.</li>}
      </ul>
    </>
  );
}

function summarise(p: Any): string {
  if (!p) return "";
  if (p.net !== undefined) return `net ${Number(p.net).toFixed(2)}`;
  if (p.reason) return String(p.reason);
  return Object.entries(p).slice(0, 4).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v).slice(0, 60) : v}`).join(", ");
}
