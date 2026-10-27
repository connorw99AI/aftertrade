"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { BacktestTrades } from "@/components/BacktestTrades";
import { BOTS, Creature, CREATURES } from "@/components/Creature";
import { EquityChart } from "@/components/EquityChart";
import { Button, DateInput, EmptyState, Panel, PctDir, Pnl, Stat, Stats, Table } from "@/components/ui";
import { Any, api, apiBase, confirmAction, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const TFS = ["M5", "M15", "H1"];
const isoDay = (d: Date) => d.toISOString().slice(0, 10);
const daysAgo = (n: number) => isoDay(new Date(Date.now() - n * 86400000));
const ukDay = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "–");

/** One bar per metal and timeframe across the dates downloaded: what history exists at a glance. */
function Coverage({ ds }: { ds: Any[] }) {
  const have = ds.filter((d) => d.start && d.end);
  if (!have.length) return <EmptyState>No price history downloaded yet. Pick dates and download from MT5.</EmptyState>;
  const t0 = Math.min(...have.map((d) => new Date(d.start).getTime())), t1 = Math.max(...have.map((d) => new Date(d.end).getTime()));
  const at = (t: number) => `${((t - t0) / (t1 - t0 || 1)) * 100}%`;
  return (
    <div className="coverage">
      <div className="coverage-axis"><span /><div><span>{ukDay(new Date(t0).toISOString())}</span><span>{ukDay(new Date(t1).toISOString())}</span></div><span /></div>
      {["GOLD", "SILVER"].flatMap((r) => TFS.map((tf) => {
        const c = ds.find((d) => d.role === r && d.timeframe === tf);
        const ok = c?.start && c?.end;
        return (
          <div key={r + tf} className="coverage-row">
            <span className={`metal-${r}`}>{r === "GOLD" ? "Gold" : "Silver"} {tf}</span>
            <div className={`coverage-track ${r}`} title={ok ? `${ukDay(c.start)} to ${ukDay(c.end)}, ${c.rows?.toLocaleString("en-GB")} bars` : "none"}>
              {ok && <i style={{ left: at(new Date(c.start).getTime()), width: `calc(${at(new Date(c.end).getTime())} - ${at(new Date(c.start).getTime())})` }} />}
            </div>
            <span className="muted">{ok ? `${c.rows?.toLocaleString("en-GB")} bars${c.downloads > 1 ? `, ${c.downloads} merged` : ""}` : "none"}</span>
          </div>
        );
      }))}
      <p className="tiny muted">Each bar spans the first to the last bar on file; downloads of different periods are merged, so a missing stretch between two downloads is not drawn.</p>
    </div>
  );
}

const mb = (b: number) => `${(b / 1e6).toFixed(b < 1e7 ? 1 : 0)} MB`;

/** Every download on disk, with delete. Live trading never reads these files; finished backtest results stay. */
function Downloads({ onChange, busy }: { onChange: () => void; busy: boolean }) {
  const { data, reload } = usePoll<Any>("/api/data/downloads", 0);
  const [err, setErr] = useState<string | null>(null);
  const rows: Any[] = data?.downloads ?? [];
  async function remove(versions: string[] | "all", what: string, bytes: number) {
    const name = await confirmAction({ title: `Delete ${what}?`, tone: "danger", confirmLabel: "Delete",
      consequences: [`Frees ${mb(bytes)} on disk.`, "Backtests over those dates need the history downloaded again.",
        "Finished backtest results and live trading are not affected."] });
    if (!name) return;
    setErr(null);
    try { await api.post("/api/data/delete", versions === "all" ? { all: true, operator: name } : { versions, operator: name }); reload(); onChange(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  if (!rows.length) return null;
  return (
    <details className="downloads">
      <summary>{rows.length} download{rows.length === 1 ? "" : "s"} on disk, {mb(data.total_bytes)}: view or delete</summary>
      {err && <div className="notice notice-danger">{err}</div>}
      <Table><thead><tr><th>Metal</th><th>Chart</th><th>From</th><th>To</th><th className="n">Bars</th><th className="n">Size</th><th>Downloaded</th><th /></tr></thead>
        <tbody>{rows.map((d) => (
          <tr key={d.version}><td className={`metal-${d.role}`}>{d.role === "GOLD" ? "Gold" : "Silver"}</td><td>{d.timeframe}</td>
            <td>{ukDay(d.start)}</td><td>{ukDay(d.end)}</td><td className="n">{d.rows?.toLocaleString("en-GB") ?? "–"}</td><td className="n">{mb(d.bytes)}</td>
            <td>{when(d.retrieved_at)}</td>
            <td><Button size="sm" tone="ghost" disabled={busy} onClick={() => remove([d.version], `${d.role === "GOLD" ? "gold" : "silver"} ${d.timeframe} ${ukDay(d.start)} to ${ukDay(d.end)}`, d.bytes)}>Delete</Button></td></tr>
        ))}</tbody></Table>
      <div className="actions" style={{ marginTop: 8 }}>
        <Button tone="danger" disabled={busy} onClick={() => remove("all", `all ${rows.length} downloads`, data.total_bytes)}>Delete all downloaded history</Button>
        {busy && <span className="muted small">Wait for downloads and backtests to finish before deleting.</span>}
      </div>
    </details>
  );
}

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
  const ready = (data?.bots ?? []).filter((b: Any) => b.ready).length;
  return (
    <Panel title="Price history from MT5" as="h2" tools={
      <form className="inline" onSubmit={(e) => { e.preventDefault(); download(); }}>
        <DateInput label="From" value={from} onChange={setFrom} />
        <DateInput label="To" value={to} onChange={setTo} />
        <Button tone="primary" disabled={f?.state === "RUNNING"}>{f?.state === "RUNNING" ? `Downloading ${f.done.length + f.failed.length}/${f.total}…` : "Download from MT5"}</Button>
      </form>}>
      {err && <div className="notice notice-danger">{err}</div>}
      {f?.failed?.length > 0 && <div className="notice notice-danger">Some downloads failed: {f.failed.join("; ")}. MT5 only returns as much history as its &quot;Max bars in chart&quot; setting allows (Tools → Options → Charts).</div>}
      <Coverage ds={data?.datasets ?? []} />
      <Downloads onChange={reload} busy={f?.state === "RUNNING"} />
      <p className="muted small">{ready} of {data?.bots?.length ?? 24} bots have all the history they need. Add an older year to test on data the bots have never seen.</p>
    </Panel>
  );
}

function RunDetail({ detail }: { detail: Any }) {
  const m = detail.metrics;
  if (detail.status === "FAILED") return <div className="notice notice-danger">{detail.error ?? "failed"}</div>;
  if (!m) return <p className="empty">Running…</p>;
  return (
    <>
      {detail.reliability?.map((r: string) => <div key={r} className={`notice ${r.startsWith("DRAWDOWN") ? "notice-danger" : ""}`}>{r}</div>)}
      <Stats>
        <Stat label="Trades" value={m.n_trades} />
        <Stat label="Net" value={<Pnl v={m.net_pnl} />} sub={m.account_currency && !String(m.account_currency).startsWith("GBP") ? m.account_currency : undefined} />
        <Stat label="Return" value={<PctDir v={m.total_return * 100} />} />
        <Stat label="Max drawdown" value={`${(m.max_drawdown * 100).toFixed(1)}%`} />
        <Stat label="Win rate" value={m.win_rate !== undefined ? `${(m.win_rate * 100).toFixed(1)}%` : "–"} />
        <Stat label="Profit factor" value={m.profit_factor ?? "–"} />
        <Stat label="Costs" value={money((m.total_spread_slippage_cost ?? 0) * -1 + (m.total_commission ?? 0) + (m.total_swap ?? 0), true)} />
        <Stat label="Starting capital" value={money(m.initial_capital)} />
        <Stat label="Drawdown restarts" value={m.drawdown_restarts ?? 0} />
      </Stats>
      {m.sample_warning && <p className="muted">{m.sample_warning}</p>}
      <EquityChart points={detail.equity || []} />
      <p><Link href={`/bot?id=${detail.strategy_id}&tab=review&source=backtest:${detail.id}`}>Review these trades and get suggestions →</Link></p>
      <details><summary>Diagnostics and assumptions</summary><pre>{JSON.stringify({ diagnostics: detail.diagnostics, assumptions: detail.config }, null, 1)}</pre></details>
    </>
  );
}

function BacktestVsDemo() {
  const { data: cmp } = usePoll<Any[]>("/api/compare", 15000);
  return (
    <>
      <h2>Backtest vs demo</h2>
      <p className="muted">Latest backtest per bot next to its forward demo result. They are never blended. A big gap usually means the backtest was too optimistic.</p>
      <Table>
        <thead><tr><th>Bot</th><th>Backtest data</th><th className="n">Backtest trades</th><th className="n">Backtest return</th><th className="n">Backtest max fall</th>
          <th className="n">Profit factor</th><th className="n">Demo trades</th><th className="n">Demo net</th></tr></thead>
        <tbody>{cmp?.map((r) => { const m = r.latest_backtest?.metrics; return (
          <tr key={r.bot_id}><td><Link href={`/bot?id=${r.bot_id}`} className="who"><Creature bot={r.bot_id} size={24} mood="still" /> {CREATURES[r.bot_id]?.name}</Link></td>
            <td className="small">{r.latest_backtest ? (r.latest_backtest.dataset === "synthetic" ? "synthetic (machinery check)" : "MT5 history") : <span className="muted">none yet</span>}</td>
            <td className="n">{m?.n_trades ?? "–"}</td><td className="n">{m ? <PctDir v={m.total_return * 100} /> : "–"}</td>
            <td className="n">{m ? `${(m.max_drawdown * 100).toFixed(1)}%` : "–"}</td><td className="n">{m?.profit_factor != null ? Number(m.profit_factor).toFixed(2) : "–"}</td>
            <td className="n">{r.forward_demo.closed_trades}</td><td className="n">{r.forward_demo.closed_trades ? <Pnl v={r.forward_demo.net} /> : <span className="muted">no trades yet</span>}</td></tr>); })}</tbody>
      </Table>
    </>
  );
}

export default function Research() {
  const { data: runs, reload } = usePoll<Any[]>("/api/backtests", 3000);
  const [sel, setSel] = useState<number | null>(null);
  const [detail, setDetail] = useState<Any>(null);
  const [form, setForm] = useState({ strategy_id: "S09L", dataset: "mt5", spread_multiplier: 1, slippage_multiplier: 1, commission_per_lot_side: 0, start: "", end: "" });
  const [err, setErr] = useState<string | null>(null);
  // the newest run is selected until you pick another, so the right panel is never empty
  const shown = sel ?? (runs?.length ? Math.max(...runs.map((r) => r.id)) : null);
  useEffect(() => { if (shown) api.get<Any>(`/api/backtests/${shown}`).then(setDetail).catch((e) => setErr(String(e))); else setDetail(null); }, [shown, runs]);
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
    const name = await confirmAction({ title: "Delete all backtest results?", tone: "danger", confirmLabel: "Delete results",
      consequences: [`${runs?.length ?? 0} runs are deleted.`, "Downloaded price history is kept."] });
    if (!name) return;
    try { await api.post("/api/backtests/clear"); setSel(null); setDetail(null); reload(); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  }
  const num = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: Number(e.target.value) });
  return (
    <>
      <div className="banner-replay">Historical replay. Nothing on this page is forward demo activity.</div>
      <div className="page-head"><div><h1>Research</h1>
        <p className="lede">Backtests run each bot with its current rules and your live risk settings over downloaded MT5 history. Results are in your account currency.</p></div></div>
      <DataPanel />
      <form className="inline panel" style={{ marginTop: 16 }} onSubmit={(e) => { e.preventDefault(); run(false); }}>
        <label>Bot<select value={form.strategy_id} onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}>{BOTS.map((i) => <option key={i} value={i}>{CREATURES[i].name}</option>)}</select></label>
        <label>Data<select value={form.dataset} onChange={(e) => setForm({ ...form, dataset: e.target.value })}>
          <option value="mt5">MT5 history</option><option value="synthetic">Synthetic (machinery check only)</option></select></label>
        <label>Spread ×<input type="number" step="0.1" value={form.spread_multiplier} onChange={num("spread_multiplier")} style={{ width: 80 }} /></label>
        <label>Slippage ×<input type="number" step="0.1" value={form.slippage_multiplier} onChange={num("slippage_multiplier")} style={{ width: 80 }} /></label>
        <label>Commission / lot / side<input type="number" step="0.1" value={form.commission_per_lot_side} onChange={num("commission_per_lot_side")} style={{ width: 90 }} /></label>
        <DateInput label="From" value={form.start} onChange={(v) => setForm({ ...form, start: v })} />
        <DateInput label="To" value={form.end} onChange={(v) => setForm({ ...form, end: v })} />
        <Button tone="primary">Run this bot</Button>
        <Button type="button" onClick={() => run(true)}>Run all {BOTS.length} bots</Button>
        <Button type="button" tone="danger" disabled={running || !runs?.length} onClick={clear}>Clear old results</Button>
        <a className="btn" href={`${apiBase()}/api/backtests/report.md`}>Export for review</a>
      </form>
      {err && <div className="notice notice-danger">{err}</div>}
      <div className="bt-layout">
        <Panel title="Runs">
          {!runs?.length ? <EmptyState>No runs yet. Run one bot, or all of them, above.</EmptyState> : (
            <Table><thead><tr><th>Bot</th><th>Data</th><th>Status</th><th className="n">Trades</th><th className="n">Return</th></tr></thead>
              <tbody>{runs.map((r) => <tr key={r.id} onClick={() => setSel(r.id)} className={`clickable ${shown === r.id ? "sel" : ""}`} title={`Run ${r.id}, ${when(r.created_at)}`}>
                <td><span className="who"><Creature bot={r.strategy_id} size={22} mood="still" /> {CREATURES[r.strategy_id]?.name ?? r.strategy_id}</span></td>
                <td className="small">{r.dataset_key === "synthetic" ? "synthetic" : "MT5"}</td>
                <td className={r.status === "FAILED" ? "err" : r.status === "DONE" ? "" : "muted"}>{r.status === "DONE" ? "done" : r.status.toLowerCase()}</td>
                <td className="n">{r.metrics?.n_trades ?? "–"}</td>
                <td className="n">{r.metrics ? <PctDir v={r.metrics.total_return * 100} /> : "–"}</td></tr>)}</tbody></Table>)}
        </Panel>
        <Panel title={detail ? <>{CREATURES[detail.strategy_id]?.name} <span className="muted small">run {detail.id}</span></> : "Run"}>
          {!detail ? <EmptyState>Nothing to show until a backtest has run.</EmptyState> : <RunDetail detail={detail} />}
        </Panel>
      </div>
      {detail?.trades && <>
        <h2>Trades in this run{detail.trades.length ? ` (${detail.trades.length})` : ""}</h2>
        <BacktestTrades trades={detail.trades} restarts={detail.diagnostics?.drawdown_restarts ?? []} />
      </>}
      <BacktestVsDemo />
    </>
  );
}
