"use client";
import { CREATURES } from "@/components/Creature";
import { Any, money } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Compare() {
  const { data } = usePoll<Any[]>("/api/compare", 15000);
  const { data: rk } = usePoll<Any>("/api/ranking", 15000);
  return (
    <>
      <h1>Live candidate ranking</h1>
      <p className="muted">Rule: {rk?.rule ?? "…"}. Demo trades only. Picking the best of ten bots still favours luck, so treat a top rank as a shortlist, not proof.</p>
      <div className="table-wrap"><table>
        <thead><tr><th className="n">Rank</th><th>Bot</th><th className="n">Trades</th><th className="n">Net</th><th className="n">Return</th><th className="n">Max DD</th><th className="n">Win rate</th><th className="n">Profit factor</th><th>Status</th></tr></thead>
        <tbody>{rk?.rows?.map((r: Any) => (
          <tr key={r.bot_id}><td className="n">{r.rank ?? "–"}</td><td>{CREATURES[r.bot_id]?.name} ({r.bot_id})</td>
            <td className="n">{r.closed_trades}</td><td className={`n ${r.net >= 0 ? "gain" : "loss"}`}>{money(r.net, true)}</td>
            <td className="n">{r.return_pct == null ? "–" : `${r.return_pct.toFixed(2)}%`}</td><td className="n">{r.max_drawdown_pct.toFixed(2)}%</td>
            <td className="n">{r.win_rate_pct == null ? "–" : `${r.win_rate_pct}%`}</td><td className="n">{r.profit_factor ?? "–"}</td>
            <td className={r.eligible ? "gain" : "muted"}>{r.eligible ? "eligible" : r.not_eligible_because.join(", ")}</td></tr>))}</tbody>
      </table></div>

      <h1>Strategy comparison</h1>
      <p className="muted">Latest backtest and forward demo results sit in separate columns and are never blended. Backtests on synthetic data only check the machinery.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>Bot</th><th>Style</th><th>Backtest data</th><th className="n">BT trades</th><th className="n">BT return</th><th className="n">BT max DD</th><th className="n">BT Sharpe</th><th>BT warnings</th><th className="n">Demo trades</th><th className="n">Demo net</th></tr></thead>
        <tbody>{data?.map((r) => { const m = r.latest_backtest?.metrics; return (
          <tr key={r.bot_id}><td>{CREATURES[r.bot_id]?.name} ({r.bot_id})</td><td>{r.style.toLowerCase()}</td><td>{r.latest_backtest?.dataset ?? "none"}</td>
            <td className="n">{m?.n_trades ?? "–"}</td><td className="n">{m ? `${(m.total_return * 100).toFixed(2)}%` : "–"}</td>
            <td className="n">{m ? `${(m.max_drawdown * 100).toFixed(2)}%` : "–"}</td><td className="n">{m?.sharpe_daily_ann ?? "–"}</td>
            <td className="muted">{r.latest_backtest?.reliability?.length ?? 0}</td>
            <td className="n">{r.forward_demo.closed_trades}</td><td className={`n ${r.forward_demo.net >= 0 ? "gain" : "loss"}`}>{money(r.forward_demo.net, true)}</td></tr>); })}</tbody>
      </table></div>
    </>
  );
}
