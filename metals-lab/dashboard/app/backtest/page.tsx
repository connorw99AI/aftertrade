"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { BOTS, Creature, CREATURES } from "@/components/Creature";
import { EquityChart } from "@/components/EquityChart";
import { Any, api, apiBase, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const IDS = BOTS;
const TFS = ["M1", "M5", "M15", "H1"];
const isoDay = (d: Date) => d.toISOString().slice(0, 10);
const daysAgo = (n: number) => isoDay(new Date(Date.now() - n * 86400000));

function DataPanel() {
  const { data, reload } = usePoll<Any>("/api/data/status", 3000);
  const [from, setFrom] = useState(daysAgo(365));
  const [to, setTo] = useState(isoDay(new Date()));
  const [err, setErr] = useState<string | null>(null);
  const f = data?.fetch;
  async function download() {
    setErr(null);
    try { await api.post("/api/data/fetch", { start: from, end: to, timeframes: TFS }); reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  const ds: Any[] = data?.datasets ?? [];
  const cell = (role: string, tf: string) => ds.find((d) => d.role === role && d.timeframe === tf);
  const ready = (data?.bots ?? []).filter((b: Any) => b.ready).length;
  return (
    <section className="panel">
      <div className="row-between">
        <h2>Price history from MT5</h2>
        <form className="inline" onSubmit={(e) => { e.preventDefault(); download(); }}>
          <label>From<input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>To<input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></label>
          <button className="primary" disabled={f?.state === "RUNNING"}>{f?.state === "RUNNING" ? `Downloading ${f.done.length + f.failed.length}/${f.total}…` : "Download history from MT5"}</button>
        </form>
      </div>
      {err && <div className="warn danger">{err}</div>}
      {f?.failed?.length > 0 && <div className="warn danger">Some downloads failed: {f.failed.join("; ")}. MT5 only returns as much history as its &quot;Max bars in chart&quot; setting allows (Tools → Options → Charts).</div>}
      <div className="table-wrap">
        <table className="data-table">
          <thead><tr><th>Metal</th>{TFS.map((t) => <th key={t}>{t}</th>)}</tr></thead>
          <tbody>{["GOLD", "SILVER"].map((r) => (
            <tr key={r}><td>{r === "GOLD" ? "Gold" : "Silver"}</td>{TFS.map((t) => { const c = cell(r, t); return (
              <td key={t}>{c?.version ? <><b>{c.rows?.toLocaleString()}</b> bars<br /><span className="muted small">{c.start?.slice(0, 10)} → {c.end?.slice(0, 10)}{c.downloads > 1 ? ` (${c.downloads} downloads merged)` : ""}</span></> : <span className="muted">none</span>}</td>); })}</tr>))}</tbody>
        </table>
      </div>
      <p className="muted small">{ready} of {data?.bots?.length ?? 20} bots have all the history they need. Downloads of different periods are merged, so you can add an older year to test on data the bots have never seen. MT5 only returns as far back as its &quot;Max bars in chart&quot; setting allows.</p>
    </section>
  );
}

export default function Backtest() {
  const { data: runs, reload } = usePoll<Any[]>("/api/backtests", 3000);
  const [sel, setSel] = useState<number | null>(null);
  const [detail, setDetail] = useState<Any>(null);
  const [form, setForm] = useState({ strategy_id: "S09L", dataset: "mt5", spread_multiplier: 1, slippage_multiplier: 1, commission_per_lot_side: 0, start: "", end: "" });
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (sel) api.get<Any>(`/api/backtests/${sel}`).then(setDetail).catch((e) => setErr(String(e))); }, [sel, runs]);
  const running = (runs ?? []).some((r) => r.status === "RUNNING" || r.status === "QUEUED");
  async function run(all: boolean) {
    setErr(null);
    try {
      const { strategy_id, start, end, ...base } = form;
      const rest = { ...base, start: start || null, end: end || null };
      const r = all ? await api.post<Any>("/api/backtests/run-all", rest) : await api.post<Any>("/api/backtests", { strategy_id, ...rest });
      setSel(all ? r.ids[0] : r.id);
      reload();
    } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  }
  async function clear() {
    if (!window.confirm("Delete all backtest results? Downloaded price history is kept.")) return;
    try { await api.post("/api/backtests/clear"); setSel(null); setDetail(null); reload(); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  }
  const num = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: Number(e.target.value) });
  const m = detail?.metrics;
  return (
    <>
      <div className="banner-replay">Historical replay. Nothing on this page is forward demo activity.</div>
      <div className="page-head"><h1>Backtest</h1>
        <p className="muted">Runs each bot with its current rules and your live risk settings (minimum lots, daily stops, drawdown pause) over downloaded MT5 history. Results are in your account currency.</p></div>
      <DataPanel />
      <form className="inline panel" onSubmit={(e) => { e.preventDefault(); run(false); }}>
        <label>Bot<select value={form.strategy_id} onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}>{IDS.map((i) => <option key={i} value={i}>{CREATURES[i].name} ({i})</option>)}</select></label>
        <label>Data<select value={form.dataset} onChange={(e) => setForm({ ...form, dataset: e.target.value })}>
          <option value="mt5">MT5 history</option><option value="synthetic">Synthetic (machinery check only)</option></select></label>
        <label>Spread ×<input type="number" step="0.1" value={form.spread_multiplier} onChange={num("spread_multiplier")} style={{ width: 80 }} /></label>
        <label>Slippage ×<input type="number" step="0.1" value={form.slippage_multiplier} onChange={num("slippage_multiplier")} style={{ width: 80 }} /></label>
        <label>Commission / lot / side<input type="number" step="0.1" value={form.commission_per_lot_side} onChange={num("commission_per_lot_side")} style={{ width: 90 }} /></label>
        <label>From<input type="date" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></label>
        <label>To<input type="date" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></label>
        <button className="primary">Run this bot</button>
        <button type="button" onClick={() => run(true)}>Run all 20 bots</button>
        <button type="button" className="btn-stop" disabled={running || !runs?.length} onClick={clear}>Clear old results</button>
        <a className="button-link" href={`${apiBase()}/api/backtests/report.md`}>Export for review</a>
      </form>
      {err && <div className="warn danger">{err}</div>}
      <div className="bt-layout">
        <div className="panel">
          <h3>Runs</h3>
          {!runs?.length ? <p className="empty">No runs yet.</p> : (
            <div className="table-wrap"><table><thead><tr><th>#</th><th>Bot</th><th>Data</th><th>Status</th><th className="n">Trades</th><th className="n">Return</th></tr></thead>
              <tbody>{runs.map((r) => <tr key={r.id} onClick={() => setSel(r.id)} className={`clickable ${sel === r.id ? "sel" : ""}`}>
                <td>{r.id}</td><td><span className="who"><Creature bot={r.strategy_id} size={22} /> {r.strategy_id}</span></td>
                <td className="small">{r.dataset_key === "synthetic" ? "synthetic" : r.dataset_key === "mt5" ? "MT5" : "MT5 history"}</td>
                <td className={r.status === "FAILED" ? "loss" : r.status === "DONE" ? "" : "muted"}>{r.status.toLowerCase()}</td>
                <td className="n">{r.metrics?.n_trades ?? "–"}</td>
                <td className={`n ${(r.metrics?.total_return ?? 0) >= 0 ? "gain" : "loss"}`}>{r.metrics ? `${(r.metrics.total_return * 100).toFixed(1)}%` : "–"}</td></tr>)}</tbody></table></div>)}
        </div>
        <div className="panel">
          <h3>{detail ? <>Run {detail.id}: {CREATURES[detail.strategy_id]?.name}</> : "Run"}</h3>
          {!detail ? <p className="empty">Select a run.</p> : detail.status === "FAILED" ? <div className="warn danger">{detail.error ?? "failed"}</div> : !m ? <p className="empty">Running…</p> : <>
            {detail.reliability?.map((r: string) => <div key={r} className={`warn ${r.startsWith("DRAWDOWN") ? "danger" : ""}`}>{r}</div>)}
            <div className="metrics">
              <div className="metric"><span>Trades</span><b>{m.n_trades}</b></div>
              <div className="metric"><span>Net ({m.account_currency ?? "acct"})</span><b className={m.net_pnl >= 0 ? "gain" : "loss"}>{money(m.net_pnl, true)}</b></div>
              <div className="metric"><span>Return</span><b>{(m.total_return * 100).toFixed(1)}%</b></div>
              <div className="metric"><span>Max drawdown</span><b>{(m.max_drawdown * 100).toFixed(1)}%</b></div>
              <div className="metric"><span>Win rate</span><b>{m.win_rate !== undefined ? `${(m.win_rate * 100).toFixed(1)}%` : "–"}</b></div>
              <div className="metric"><span>Profit factor</span><b>{m.profit_factor ?? "–"}</b></div>
              <div className="metric"><span>Costs</span><b>{money((m.total_spread_slippage_cost ?? 0) * -1 + (m.total_commission ?? 0) + (m.total_swap ?? 0), true)}</b></div>
              <div className="metric"><span>Starting capital</span><b>{money(m.initial_capital)}</b></div>
            </div>
            {m.sample_warning && <p className="muted">{m.sample_warning}</p>}
            <EquityChart points={detail.equity || []} />
            <p><Link href={`/bot?id=${detail.strategy_id}&tab=review&source=backtest:${detail.id}`}>Review these trades and get suggestions →</Link></p>
            <details><summary>Diagnostics and assumptions</summary><pre>{JSON.stringify({ diagnostics: detail.diagnostics, assumptions: detail.config }, null, 1)}</pre></details>
          </>}
        </div>
      </div>
      {detail?.trades?.length > 0 && <>
        <h2>Trades in this run</h2>
        <div className="table-wrap"><table><thead><tr><th>Entry</th><th>Exit</th><th>Trade</th><th className="n">Net</th><th className="n">R</th><th>Exit reason</th><th>Why it entered</th></tr></thead>
          <tbody>{detail.trades.slice(0, 400).map((t: Any) => <tr key={t.trade_id}><td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td>
            <td>{t.side === "LONG" ? "▲" : "▼"} {t.volume} {t.symbol}</td>
            <td className={`n num ${t.net_pnl >= 0 ? "gain" : "loss"}`}>{money(t.net_pnl, true)}</td><td className="n">{t.r_multiple != null ? Number(t.r_multiple).toFixed(2) : "–"}</td>
            <td>{String(t.exit_reason).replace(/_/g, " ").toLowerCase()}{t.ambiguous_exit ? " (ambiguous bar)" : ""}</td>
            <td className="muted small">{t.entry_rationale}</td></tr>)}</tbody></table></div>
      </>}
    </>
  );
}
