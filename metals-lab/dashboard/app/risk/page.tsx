"use client";
import { useState } from "react";
import { Any, api, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Risk() {
  const { data: r, reload } = usePoll<Any>("/api/risk", 6000);
  const [note, setNote] = useState<string | null>(null);
  const [confirmKill, setConfirmKill] = useState(false);
  async function act(path: string, body: Any = {}) {
    try { await api.post(path, body); setNote(null); reload(); } catch (e) { setNote(e instanceof Error ? e.message : String(e)); }
  }
  if (!r) return <p className="empty">Loading…</p>;
  const open = r.alerts.filter((a: Any) => !a.resolved_at);
  return (
    <>
      <h1>Risk centre</h1>
      <div className="grid2">
        <div className="panel">
          <h3>Controls</h3>
          <p className="muted">Pause stops new entries; open positions keep their server-side stops and strategy exits. Kill: {r.kill_switch_behaviour}.</p>
          <div className="actions">
            <button onClick={() => act("/api/risk/pause-all", { reason: "dashboard" })} disabled={r.global_pause}>Pause all bots</button>
            <button onClick={() => act("/api/risk/resume-all", { reason: "dashboard" })} disabled={!r.global_pause}>Resume all bots</button>
            <button onClick={() => act("/api/risk/disarm", { reason: "dashboard disarm" })}>Disarm</button>
            {!confirmKill ? <button className="danger" onClick={() => setConfirmKill(true)} disabled={r.kill}>Kill switch</button> :
              <><span>Close every bot position and disarm?</span><button className="danger" onClick={() => { setConfirmKill(false); act("/api/risk/kill", { reason: "dashboard kill" }); }}>Yes, kill</button>
                <button onClick={() => setConfirmKill(false)}>Cancel</button></>}
          </div>
          <p className="muted">Arming is only possible from the command line on the trading computer (metals-lab arm).</p>
          {note && <div className="warn danger">{note}</div>}
        </div>
        <div className="panel">
          <h3>Account and capital</h3>
          {r.account ? <div className="metrics">
            <div className="metric"><span>Balance</span><b>{money(r.account.balance)}</b></div>
            <div className="metric"><span>Equity</span><b>{money(r.account.equity)}</b></div>
            <div className="metric"><span>Margin used</span><b>{money(r.account.margin)}</b></div>
            <div className="metric"><span>Margin level</span><b>{r.account.margin_level ? `${r.account.margin_level.toFixed(0)}%` : "–"}</b></div>
            <div className="metric"><span>Starting snapshot</span><b>{r.capital_snapshot ? money(r.capital_snapshot.starting_balance) : "not armed yet"}</b></div>
            <div className="metric"><span>Unallocated funds</span><b>{money(r.unallocated_pool)}</b></div>
          </div> : <p className="empty">No terminal connected.</p>}
          {r.unallocated_events?.length > 0 && <p className="muted">{r.unallocated_events.length} balance operation(s) detected. They are never distributed automatically; use metals-lab allocate.</p>}
        </div>
      </div>
      <h2>Open alerts</h2>
      {!open.length ? <p className="empty">No open alerts.</p> : (
        <div className="table-wrap"><table><thead><tr><th>Time</th><th>Severity</th><th>Alert</th><th>Blocks entries</th><th /></tr></thead>
          <tbody>{open.map((a: Any) => <tr key={a.id}><td>{when(a.ts)}</td><td>{a.severity}</td><td>{a.message}<div className="muted">{a.code}</div></td>
            <td>{a.blocks_entries ? "yes" : "no"}</td><td><button onClick={() => act(`/api/alerts/${a.code}/resolve`, { reason: "reviewed in dashboard" })}>Mark reviewed</button></td></tr>)}</tbody></table></div>)}
      <h2>Limits</h2>
      <div className="table-wrap"><table><tbody>{Object.entries(r.limits).map(([k, v]) => <tr key={k}><td>{k.replaceAll("_", " ")}</td><td className="n">{typeof v === "object" ? JSON.stringify(v) : String(v)}</td></tr>)}</tbody></table></div>
      <p className="muted">Limits are planning numbers against each bot&apos;s own equity, not guarantees: gaps and slippage can make realized losses larger.</p>
      <h2>Audit log</h2>
      <div className="table-wrap"><table><thead><tr><th>Time</th><th>Bot</th><th>Action</th><th>By</th><th>Change</th></tr></thead>
        <tbody>{r.audit_log.map((a: Any) => <tr key={a.id}><td>{when(a.created_at)}</td><td>{a.bot_id ?? "system"}</td><td>{a.action}</td><td>{a.operator}</td><td className="muted">{a.new_json}</td></tr>)}</tbody></table></div>
    </>
  );
}
