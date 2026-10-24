"use client";
import { Any, money } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

/** Side-by-side backtest of a bot as it is now and with one suggested change. */
export function Compare2({ ids }: { ids: { baseline_id: number; candidate_id: number; change: Any } }) {
  const { data: runs } = usePoll<Any[]>("/api/backtests", 3000);
  const a = runs?.find((r) => r.id === ids.baseline_id), c = runs?.find((r) => r.id === ids.candidate_id);
  if (!a || !c || [a.status, c.status].some((s) => s === "RUNNING" || s === "QUEUED")) return <p className="muted">Testing on history…</p>;
  if (a.status === "FAILED" || c.status === "FAILED") return <div className="warn danger">{a.error || c.error}</div>;
  const row = (k: string, f: (m: Any) => string) => <tr><td>{k}</td><td className="n">{f(a.metrics)}</td><td className="n">{f(c.metrics)}</td></tr>;
  const better = c.metrics.net_pnl > a.metrics.net_pnl && c.metrics.max_drawdown >= a.metrics.max_drawdown * 1.1;
  return (
    <div className="compare2">
      <table><thead><tr><th /><th className="n">Now</th><th className="n">With change</th></tr></thead><tbody>
        {row("Trades", (m) => String(m.n_trades))}
        {row("Net", (m) => money(m.net_pnl, true))}
        {row("Max drawdown", (m) => `${(m.max_drawdown * 100).toFixed(1)}%`)}
        {row("Profit factor", (m) => String(m.profit_factor ?? "–"))}
        {row("Win rate", (m) => `${((m.win_rate ?? 0) * 100).toFixed(1)}%`)}
      </tbody></table>
      <p className={better ? "gain" : "muted"}>{better ? "Better on this history without a much deeper drawdown." : "Not clearly better on this history."} Results on the same data the idea came from are optimistic.</p>
    </div>
  );
}

