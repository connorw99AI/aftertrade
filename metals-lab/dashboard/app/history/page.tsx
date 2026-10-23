"use client";
import Link from "next/link";
import { useState } from "react";
import { Creature, CREATURES } from "@/components/Creature";
import { Any, apiBase, minutes, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

function exitKind(t: Any): string {
  const r = String(t.exit_rationale || "").toLowerCase();
  if (r.includes("manual close") || r.includes("kill switch")) return "Manual";
  if (r.includes("weekend")) return "Weekend";
  if (r.includes("max holding")) return "Time limit";
  if (r.includes("server-side")) return r.includes("[tp") ? "Take-profit" : r.includes("[sl") ? "Stop-loss" : "Stop/target";
  return "Strategy exit";
}

export default function History() {
  const [bot, setBot] = useState("");
  const [symbol, setSymbol] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const qs = new URLSearchParams();
  if (bot) qs.set("bot", bot);
  if (symbol) qs.set("symbol", symbol);
  if (since) qs.set("since", since);
  if (until) qs.set("until", `${until}T23:59:59`);
  const q = qs.toString();
  const { data } = usePoll<Any>(`/api/history${q ? `?${q}` : ""}`, 15000);
  const rows: Any[] = data?.trades ?? [];
  const tot = data?.totals;
  return (
    <>
      <div className="page-head row-between">
        <div><h1>Trade history</h1><p className="muted">Closed demo trades, net of spread, commission and swap. R = result divided by what the trade risked at its stop.</p></div>
        <a className="button-link" href={`${apiBase()}/api/history.csv${q ? `?${q}` : ""}`}>Download CSV</a>
      </div>
      <form className="inline filters" onSubmit={(e) => e.preventDefault()}>
        <label>Bot<select value={bot} onChange={(e) => setBot(e.target.value)}><option value="">All</option>
          {Object.keys(CREATURES).map((b) => <option key={b} value={b}>{CREATURES[b].name}</option>)}</select></label>
        <label>Metal<select value={symbol} onChange={(e) => setSymbol(e.target.value)}><option value="">Both</option>
          <option value="XAUUSD">Gold</option><option value="XAGUSD">Silver</option></select></label>
        <label>From<input type="date" value={since} onChange={(e) => setSince(e.target.value)} /></label>
        <label>To<input type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
      </form>
      {tot && (
        <div className="metrics">
          <div className="metric"><span>Trades</span><b>{tot.count}</b></div>
          <div className="metric"><span>Net</span><b className={tot.net >= 0 ? "gain" : "loss"}>{money(tot.net, true)}</b></div>
          <div className="metric"><span>Won / lost</span><b>{tot.wins} / {tot.losses}</b></div>
          <div className="metric"><span>Win rate</span><b>{tot.count ? `${Math.round((tot.wins / tot.count) * 100)}%` : "–"}</b></div>
        </div>
      )}
      {rows.length === 0 ? <p className="empty">No closed trades{q ? " for this filter" : " yet"}.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Closed</th><th>Bot</th><th>Trade</th><th className="n">Entry</th><th className="n">Exit</th><th>Held</th>
              <th className="n">Net</th><th className="n">R</th><th>Exit</th><th>Why it entered</th></tr></thead>
            <tbody>
              {rows.map((t) => {
                const d = String(t.symbol).startsWith("XAG") ? 3 : 2;
                const pair = String(t.symbol).includes(",");
                return (
                  <tr key={t.trade_id}>
                    <td>{when(t.exit_time)}</td>
                    <td><Link href={`/bot?id=${t.bot_id}`} className="who"><Creature bot={t.bot_id} size={24} /> {CREATURES[t.bot_id]?.name}</Link></td>
                    <td>{pair ? "Gold/silver pair (2 legs)" : <>{t.side === "LONG" ? "▲" : "▼"} {t.volume} {t.symbol}</>}</td>
                    <td className="n num">{pair ? "–" : Number(t.entry_price).toFixed(d)}</td>
                    <td className="n num">{pair ? "–" : Number(t.exit_price).toFixed(d)}</td>
                    <td>{minutes(t.minutes_held)}</td>
                    <td className={`n num ${t.net >= 0 ? "gain" : "loss"}`}>{money(t.net, true)}{t.provisional ? "*" : ""}</td>
                    <td className="n num">{t.r_multiple ?? "–"}</td>
                    <td title={t.exit_rationale}>{exitKind(t)}</td>
                    <td className="small muted">{t.entry_rationale}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted small">* provisional: costs not yet final in the terminal&apos;s deal history.</p>
        </div>
      )}
    </>
  );
}
