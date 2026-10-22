"use client";
import { CREATURES } from "@/components/Creature";
import { Any, money } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Compare() {
  const { data } = usePoll<Any[]>("/api/compare", 15000);
  return (
    <>
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
