"use client";
import { Fragment, useState } from "react";
import { Any, money, when } from "@/lib/api";

const PAGE = 100;
const px = (v: unknown, sym: string) => (v == null || Number.isNaN(Number(v)) ? "–" : Number(v).toFixed(String(sym).startsWith("XAG") ? 3 : 2));
const reason = (r: string) => String(r).replace(/_/g, " ").toLowerCase();

/** Every trade of one backtest run: entry, exit, stop, target and result; click a row for why it entered and exited.
 *  Drawdown restarts (the backtest pressing Start for you) are listed above the table. */
export function BacktestTrades({ trades, restarts = [] }: { trades: Any[]; restarts?: Any[] }) {
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  if (!trades.length) return <p className="empty">This run made no trades.</p>;
  const pages = Math.ceil(trades.length / PAGE);
  const rows = trades.slice(page * PAGE, (page + 1) * PAGE);
  return (
    <>
      {restarts.length > 0 && (
        <div className="warn">Restarted {restarts.length} time{restarts.length > 1 ? "s" : ""} after the drawdown pause, as if you pressed Start the next day:{" "}
          {restarts.map((r: Any) => `${r.at} (balance ${money(r.equity)})`).join(", ")}.</div>
      )}
      <div className="table-wrap"><table className="history">
        <thead><tr><th>Opened</th><th>Closed</th><th>Trade</th><th className="n">Entry</th><th className="n">Exit</th>
          <th className="n">Stop</th><th className="n">Target</th><th className="n">Profit</th><th className="n">R</th><th>Exit</th></tr></thead>
        <tbody>{rows.map((t: Any) => {
          const id = String(t.trade_id);
          return (
            <Fragment key={id}>
              <tr className={`clickable ${open === id ? "sel" : ""}`} onClick={() => setOpen(open === id ? null : id)}>
                <td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td>
                <td>{t.side === "LONG" ? "▲" : "▼"} {t.volume} {t.symbol}{t.group_id ? <span className="muted"> (pair leg)</span> : null}</td>
                <td className="n num">{px(t.entry_price, t.symbol)}</td><td className="n num">{px(t.exit_price, t.symbol)}</td>
                <td className="n num">{px(t.stop, t.symbol)}</td><td className="n num">{t.target ? px(t.target, t.symbol) : "none"}</td>
                <td className={`n num ${t.net_pnl >= 0 ? "gain" : "loss"}`}>{money(t.net_pnl, true)}</td>
                <td className="n">{t.r_multiple != null ? Number(t.r_multiple).toFixed(2) : "–"}</td>
                <td>{reason(t.exit_reason)}{t.ambiguous_exit ? " (ambiguous bar)" : ""}</td>
              </tr>
              {open === id && (
                <tr className="detail-row"><td colSpan={10}><div className="trade-detail">
                  <div><b>Why it entered:</b> {t.entry_rationale || "–"}<br /><b>Why it exited:</b> {t.exit_rationale || reason(t.exit_reason)}</div>
                  <div className="small muted">Held {t.bars_held} bars · costs {money(-(t.spread_slip_cost ?? 0) + (t.commission ?? 0) + (t.swap ?? 0), true)}
                    {t.entry_indicators ? <> · at entry: {Object.entries(t.entry_indicators).map(([k, v]) => `${k} ${v}`).join(", ")}</> : null}</div>
                </div></td></tr>
              )}
            </Fragment>
          );
        })}</tbody>
      </table></div>
      {pages > 1 && (
        <div className="actions pager">
          <button disabled={page === 0} onClick={() => setPage(page - 1)}>Earlier</button>
          <span className="muted small">Trades {page * PAGE + 1}–{Math.min(trades.length, (page + 1) * PAGE)} of {trades.length}</span>
          <button disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Later</button>
        </div>
      )}
    </>
  );
}
