"use client";
import { useState } from "react";
import { Any, api, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Auditor() {
  const { data, reload } = usePoll<Any>("/api/auditor/decisions?limit=150", 6000);
  const [msg, setMsg] = useState("");
  const [op, setOp] = useState("");
  const [note, setNote] = useState<string | null>(null);
  async function alert(e: React.FormEvent) {
    e.preventDefault();
    try { await api.post("/api/auditor/market-alert", { message: msg, operator: op }); setNote("Alert raised. New entries now need manual review until you resolve it in the Risk centre."); setMsg(""); reload(); }
    catch (x) { setNote(x instanceof Error ? x.message : String(x)); }
  }
  return (
    <>
      <h1>Market auditor</h1>
      <p className="muted">Every proposal passes the auditor before the risk engine. The auditor reads market data only and cannot place orders.
        Ruleset {data?.ruleset_version}. News provider: {data?.news?.provider}; when news is unknown the policy is {data?.news?.unknown_policy?.replaceAll("_", " ")}.</p>
      <form className="inline panel" onSubmit={alert}>
        <label style={{ flex: 1 }}>Raise a market alert (for example a geopolitical headline)<input value={msg} onChange={(e) => setMsg(e.target.value)} required minLength={3} /></label>
        <label>Your name<input value={op} onChange={(e) => setOp(e.target.value)} required /></label>
        <button className="primary">Raise alert</button>
      </form>
      {note && <p className="muted">{note}</p>}
      <h2>Recent decisions</h2>
      {!data?.decisions?.length ? <p className="empty">No proposals audited yet.</p> : (
        <div className="table-wrap"><table><thead><tr><th>Time</th><th>Bot</th><th>Proposal</th><th>Decision</th><th>Rules triggered</th><th>Data</th><th>News</th><th>Reassess</th><th>Evidence</th></tr></thead>
          <tbody>{data.decisions.map((d: Any) => <tr key={d.id}><td>{when(d.ts)}</td><td>{d.bot_id}</td><td>{d.symbol} {d.intent}</td>
            <td className={d.decision === "APPROVED" ? "gain" : "loss"}>{d.decision}</td><td>{(d.triggered || []).join(", ") || "none"}<div className="muted">{(d.reasons || []).join("; ")}</div></td>
            <td>{d.data_quality}</td><td>{d.news_status}</td><td>{when(d.reassess_at)}</td>
            <td><details><summary>show</summary><pre>{JSON.stringify(d.evidence, null, 1)}</pre></details></td></tr>)}</tbody></table></div>)}
    </>
  );
}
