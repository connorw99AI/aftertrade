"use client";
import { useState } from "react";
import { Any } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Settings() {
  const { data: s } = usePoll<Any>("/api/status", 5000);
  const { data: inst } = usePoll<Any>("/api/instruments", 30000);
  const [tok, setTok] = useState(() => { try { return localStorage.getItem("metals-lab-token") ?? ""; } catch { return ""; } });
  const resolved = inst?.discovery?.resolved ?? {};
  return (
    <>
      <h1>MT5 connection</h1>
      <div className="grid2">
        <div className="panel">
          <h3>Account</h3>
          <dl className="rules">
            <dt>Terminal</dt><dd>{s?.terminal_kind}</dd>
            <dt>Account type</dt><dd>{s?.account?.state} ({s?.account?.reason})</dd>
            <dt>Server</dt><dd>{s?.account?.server ?? "–"}</dd>
            <dt>Position mode</dt><dd>{s?.account?.margin_mode}{s?.account && !s.account.hedging ? ". A hedging demo account is required for independent per-bot positions." : ""}</dd>
            <dt>Account fingerprint</dt><dd>{s?.account?.fingerprint || "–"} (a hash; the login number is never shown or stored)</dd>
            <dt>Server clock</dt><dd>{s?.clock}</dd>
            <dt>Blockers if armed</dt><dd>{s?.blockers?.length ? s.blockers.join("; ") : "none"}</dd>
          </dl>
        </div>
        <div className="panel">
          <h3>Arming demo execution</h3>
          <p>Arming happens only on the trading computer, in a terminal window:</p>
          <pre>metals-lab arm --operator "your name"</pre>
          <p className="muted">It re-checks that the account is a demo, shows the starting balance and per-bot allocation, and asks you to type a phrase bound to this account. Switching accounts later disarms automatically.</p>
          <h3>API token</h3>
          <label>Only needed if the backend sets METALS_LAB_API_TOKEN<input type="password" value={tok} onChange={(e) => setTok(e.target.value)}
            onBlur={() => { try { localStorage.setItem("metals-lab-token", tok); } catch { /* storage blocked */ } }} /></label>
        </div>
      </div>
      <h2>Gold and silver contracts found in the terminal</h2>
      {Object.keys(resolved).length === 0 && <p className="empty">No instruments resolved yet.</p>}
      {Object.entries(inst?.discovery?.unresolved ?? {}).map(([k, v]) => <div key={k} className="warn danger">{k}: {String(v)}</div>)}
      <div className="table-wrap"><table>
        <thead><tr><th>Field</th>{Object.keys(resolved).map((r) => <th key={r}>{r}</th>)}</tr></thead>
        <tbody>{["symbol", "description", "contract_kind", "contract_size", "volume_min", "volume_step", "volume_max", "tick_size", "tick_value", "digits",
          "currency_profit", "currency_margin", "trade_mode_name", "filling_modes", "stops_level_points", "freeze_level_points", "swap_long", "swap_short", "bid", "ask"].map((f) => (
          <tr key={f}><td className="muted">{f.replaceAll("_", " ")}</td>{Object.values(resolved).map((sp: Any, i) => <td key={i}>{Array.isArray(sp[f]) ? sp[f].join(", ") : String(sp[f] ?? "–")}</td>)}</tr>))}</tbody>
      </table></div>
      <h3 style={{ marginTop: 18 }}>Rejected candidates</h3>
      <ul>{(inst?.discovery?.candidates ?? []).filter((c: Any) => !c.accepted).map((c: Any) => <li key={c.symbol}>{c.symbol}: {c.reasons.join("; ")}</li>)}</ul>
      <h3>Trading sessions</h3>
      {Object.keys(inst?.sessions ?? {}).length === 0 ? <p className="muted">Unknown. Run mql5/ExportSessions.mq5 in the terminal and set mt5.sessions_export, or configure sessions explicitly. Until then the auditor relies on quote freshness.</p> :
        <pre>{JSON.stringify(inst.sessions, null, 1)}</pre>}
    </>
  );
}
