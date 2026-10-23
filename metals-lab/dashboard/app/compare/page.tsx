"use client";
import Link from "next/link";
import { Creature, CREATURES } from "@/components/Creature";
import { MultiEquity } from "@/components/MultiEquity";
import { Any, money } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Compare() {
  const { data: rk } = usePoll<Any>("/api/ranking", 15000);
  const { data: cmp } = usePoll<Any[]>("/api/compare", 15000);
  const { data: hist } = usePoll<Any>("/api/history", 15000);
  const series: Record<string, [number, number][]> = {};
  for (const t of [...(hist?.trades ?? [])].reverse()) {
    const s = (series[t.bot_id] ||= []);
    if (!s.length) s.push([new Date(t.entry_time).getTime(), 0]);
    s.push([new Date(t.exit_time).getTime(), (s.at(-1)?.[1] ?? 0) + (t.net ?? 0)]);
  }
  return (
    <>
      <div className="page-head"><h1>Compare</h1>
        <p className="muted">Rule for a live candidate: {rk?.rule ?? "…"}. Picking the best of ten bots still favours luck, so treat the top rank as a shortlist, then test it on history.</p></div>
      <section className="panel"><h2>Demo results over time</h2><MultiEquity series={series} /></section>
      <h2>Ranking</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th className="n">Rank</th><th>Bot</th><th className="n">Trades</th><th className="n">Net</th><th className="n">Return</th><th className="n">Max DD</th>
            <th className="n">Win rate</th><th className="n">Profit factor</th><th>Status</th></tr></thead>
          <tbody>{rk?.rows?.map((r: Any) => (
            <tr key={r.bot_id}><td className="n">{r.rank ?? "–"}</td>
              <td><Link href={`/bot?id=${r.bot_id}`} className="who"><Creature bot={r.bot_id} size={26} /> {CREATURES[r.bot_id]?.name}</Link></td>
              <td className="n">{r.closed_trades}</td><td className={`n num ${r.net >= 0 ? "gain" : "loss"}`}>{money(r.net, true)}</td>
              <td className="n">{r.return_pct == null ? "–" : `${r.return_pct.toFixed(2)}%`}</td><td className="n">{r.max_drawdown_pct.toFixed(2)}%</td>
              <td className="n">{r.win_rate_pct == null ? "–" : `${r.win_rate_pct}%`}</td><td className="n">{r.profit_factor ?? "–"}</td>
              <td className={r.eligible ? "gain" : "muted"}>{r.eligible ? "eligible" : r.not_eligible_because.join(", ")}</td></tr>))}</tbody>
        </table>
      </div>
      <h2>Backtest vs demo</h2>
      <p className="muted">Latest backtest per bot next to its forward demo result. They are never blended. A big gap between them usually means the backtest was too optimistic.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Bot</th><th>Backtest data</th><th className="n">BT trades</th><th className="n">BT return</th><th className="n">BT max DD</th>
            <th className="n">BT profit factor</th><th className="n">Demo trades</th><th className="n">Demo net</th></tr></thead>
          <tbody>{cmp?.map((r) => { const m = r.latest_backtest?.metrics; return (
            <tr key={r.bot_id}><td><span className="who"><Creature bot={r.bot_id} size={24} /> {CREATURES[r.bot_id]?.name}</span></td>
              <td className="small">{r.latest_backtest ? (r.latest_backtest.dataset === "synthetic" ? "synthetic (machinery check)" : "MT5 history") : <Link href="/backtest">none, run one</Link>}</td>
              <td className="n">{m?.n_trades ?? "–"}</td><td className="n">{m ? `${(m.total_return * 100).toFixed(1)}%` : "–"}</td>
              <td className="n">{m ? `${(m.max_drawdown * 100).toFixed(1)}%` : "–"}</td><td className="n">{m?.profit_factor != null ? Number(m.profit_factor).toFixed(2) : "–"}</td>
              <td className="n">{r.forward_demo.closed_trades}</td><td className={`n num ${r.forward_demo.net >= 0 ? "gain" : "loss"}`}>{money(r.forward_demo.net, true)}</td></tr>); })}</tbody>
        </table>
      </div>
    </>
  );
}
