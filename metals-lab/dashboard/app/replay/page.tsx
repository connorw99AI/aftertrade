"use client";
import { useEffect, useState } from "react";
import { EquityChart } from "@/components/EquityChart";
import { Any, api, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const IDS = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10"];

export default function Replay() {
  const { data: runs, reload } = usePoll<Any[]>("/api/backtests", 4000);
  const [sel, setSel] = useState<number | null>(null);
  const [detail, setDetail] = useState<Any>(null);
  const [form, setForm] = useState({ strategy_id: "S08", dataset: "synthetic", initial_capital: 1000, spread_multiplier: 1, slippage_multiplier: 1,
    commission_per_lot_side: 3.5, synthetic_days: 365 });
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (sel) api.get<Any>(`/api/backtests/${sel}`).then(setDetail).catch((e) => setErr(String(e))); }, [sel, runs]);
  async function start(e: React.FormEvent) {
    e.preventDefault();
    try { const r = await api.post<Any>("/api/backtests", form); setSel(r.id); reload(); setErr(null); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  }
  const num = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: Number(e.target.value) });
  const m = detail?.metrics;
  return (
    <>
      <div className="banner-replay">Historical replay. Nothing on this page is forward demo activity.</div>
      <h1>Backtest and replay</h1>
      <form className="inline panel" onSubmit={start}>
        <label>Strategy<select value={form.strategy_id} onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}>{IDS.map((i) => <option key={i}>{i}</option>)}</select></label>
        <label>Data<select value={form.dataset} onChange={(e) => setForm({ ...form, dataset: e.target.value })}>
          <option value="synthetic">Synthetic (pipeline check only)</option><option value="mt5">Cached MT5 history</option></select></label>
        <label>Virtual capital<input type="number" value={form.initial_capital} onChange={num("initial_capital")} /></label>
        <label>Spread ×<input type="number" step="0.1" value={form.spread_multiplier} onChange={num("spread_multiplier")} /></label>
        <label>Slippage ×<input type="number" step="0.1" value={form.slippage_multiplier} onChange={num("slippage_multiplier")} /></label>
        <label>Commission per lot per side<input type="number" step="0.1" value={form.commission_per_lot_side} onChange={num("commission_per_lot_side")} /></label>
        <button className="primary">Run backtest</button>
      </form>
      {err && <div className="warn danger">{err}</div>}
      <div className="grid2" style={{ marginTop: 14 }}>
        <div className="panel">
          <h3>Runs</h3>
          {!runs?.length ? <p className="empty">No runs yet. Synthetic data checks the machinery; fetch MT5 history for real research.</p> : (
            <div className="table-wrap"><table><thead><tr><th>#</th><th>Strategy</th><th>Data</th><th>Status</th><th className="n">Trades</th><th className="n">Return</th></tr></thead>
              <tbody>{runs.map((r) => <tr key={r.id} onClick={() => setSel(r.id)} style={{ cursor: "pointer", background: sel === r.id ? "#302D4B" : undefined }}>
                <td>{r.id}</td><td>{r.strategy_id}</td><td>{r.dataset_key}</td><td>{r.status}</td><td className="n">{r.metrics?.n_trades ?? "–"}</td>
                <td className="n">{r.metrics ? `${(r.metrics.total_return * 100).toFixed(2)}%` : "–"}</td></tr>)}</tbody></table></div>)}
        </div>
        <div className="panel">
          <h3>Run {sel ?? ""}</h3>
          {!detail ? <p className="empty">Select a run.</p> : detail.status === "FAILED" ? <pre>{JSON.stringify(detail.diagnostics, null, 1)}</pre> : !m ? <p className="empty">Running…</p> : <>
            {detail.reliability?.map((r: string) => <div key={r} className="warn">{r}</div>)}
            <div className="metrics">
              <div className="metric"><span>Trades</span><b>{m.n_trades}</b></div>
              <div className="metric"><span>Net P&amp;L</span><b className={m.net_pnl >= 0 ? "gain" : "loss"}>{money(m.net_pnl, true)}</b></div>
              <div className="metric"><span>Max drawdown</span><b>{(m.max_drawdown * 100).toFixed(2)}%</b></div>
              <div className="metric"><span>Sharpe (daily, ann.)</span><b>{m.sharpe_daily_ann ?? "–"}</b></div>
              <div className="metric"><span>Win rate</span><b>{m.win_rate !== undefined ? `${(m.win_rate * 100).toFixed(1)}%` : "–"}</b></div>
              <div className="metric"><span>Costs (spread+slip, comm., swap)</span><b>{money((m.total_spread_slippage_cost ?? 0) * -1 + (m.total_commission ?? 0) + (m.total_swap ?? 0), true)}</b></div>
            </div>
            {m.sample_warning && <p className="muted">{m.sample_warning}</p>}
            <EquityChart points={detail.equity || []} />
            <details><summary>Diagnostics and assumptions</summary><pre>{JSON.stringify({ diagnostics: detail.diagnostics, assumptions: detail.config }, null, 1)}</pre></details>
          </>}
        </div>
      </div>
      {detail?.trades?.length > 0 && <>
        <h2>What the bot saw, and why it entered and exited</h2>
        <div className="table-wrap"><table><thead><tr><th>Entry</th><th>Exit</th><th>Symbol</th><th>Side</th><th className="n">Net</th><th>Exit reason</th><th>Entry rationale and indicators</th></tr></thead>
          <tbody>{detail.trades.slice(0, 300).map((t: Any) => <tr key={t.trade_id}><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td><td>{t.symbol}</td><td>{t.side}</td>
            <td className={`n ${t.net_pnl >= 0 ? "gain" : "loss"}`}>{money(t.net_pnl, true)}</td><td>{t.exit_reason}{t.ambiguous_exit ? " (ambiguous bar)" : ""}</td>
            <td className="muted">{t.entry_rationale}; {Object.entries(t.entry_indicators || {}).map(([k, v]) => `${k}=${typeof v === "number" ? (v as number).toFixed(3) : v}`).join(", ")}</td></tr>)}</tbody></table></div>
      </>}
    </>
  );
}
