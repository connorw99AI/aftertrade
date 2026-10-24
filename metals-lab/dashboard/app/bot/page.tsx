"use client";
import Link from "next/link";
import { BacktestTrades } from "@/components/BacktestTrades";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { botAction, BotControls, StatusBadge, Tabs } from "@/components/Bits";
import { DecisionPipeline, decisionSentence } from "@/components/DecisionPipeline";
import { EquityChart } from "@/components/EquityChart";
import { RangeBar } from "@/components/RangeBar";
import { Button, EmptyState, PctDir, Pill, Pnl, Sparkline, Stat, Stats, Table } from "@/components/ui";
import { Creature, CREATURES, SideIcon, StateSymbol, stateSymbol } from "@/components/Creature";
import { useSummary } from "@/components/Shell";
import { CHARACTERS } from "@/components/characters";
import { Compare2 } from "@/components/TestResult";
import { Any, api, confirmAction, minutes, money, pct, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

function Metric({ k, v }: { k: string; v: React.ReactNode }) {
  return <Stat label={k} value={v} />;
}

function minLot(v: Any): string | null {
  if (v && typeof v === "object") {
    const parts = Object.entries(v).filter(([, x]) => Number(x) > 0).map(([k, x]) => `${k === "GOLD" ? "gold" : "silver"} ${x}`);
    return parts.length ? `${parts.join(", ")} lot` : null;
  }
  return Number(v) > 0 ? `${v} lot` : null;
}

/* ------------------------------------------------------------------ live */
function useEquity(id: string, allocation: number | undefined) {
  const { data } = usePoll<Any>(`/api/history?bot=${id}`, 15000);
  const trades: Any[] = [...(data?.trades ?? [])].reverse();
  const start = allocation ?? 0;
  const pts: [string, number][] = trades.length ? [[trades[0].entry_time, start]] : [];
  for (const t of trades) pts.push([t.exit_time, (pts.at(-1)?.[1] ?? start) + (t.net ?? 0)]);
  return pts;
}

function LiveTab({ b, nowMin, todayPnl }: { b: Any; nowMin: number; todayPnl: number | undefined }) {
  const bk = b.book;
  const pts = useEquity(b.bot_id, bk?.allocation);
  const { data: f7 } = usePoll<Any[]>(`/api/funnel?bot=${b.bot_id}&days=7`, 30000);
  const f = f7?.[0];
  const markers = (b.rule_changes ?? []).map((c: Any) => ({ at: c.at, label: `v${c.version}` }));
  const roles = Object.keys(b.thinking?.roles ?? {}).filter((r) => { const m = b.strategy.params?.metals?.value; return !m || m === "both" || r.toLowerCase() === m; });
  const sentence = decisionSentence(b);
  return (
    <>
      <div className="stat-row">
        <Stat label="Equity" value={money(bk?.equity)} sub={bk?.allocation ? `started with ${money(bk.allocation)}` : undefined}>
          <Sparkline points={pts.map(([t, v]) => [new Date(t).getTime(), v])} markers={markers.map((m: Any) => new Date(m.at).getTime())} label="Equity after each closed trade" />
        </Stat>
        <Stat label="Today" value={<Pnl v={todayPnl} />} />
        <Stat label="Return" value={<PctDir v={b.return_pct} digits={2} />} />
        <Stat label="Trades today" value={`${b.entries_today ?? 0}${b.max_entries_per_day ? ` / ${b.max_entries_per_day}` : ""}`} />
      </div>
      <p className="details-line">Realised <Pnl v={bk?.realized} /> · open <Pnl v={bk?.unrealized} /> · free {money(bk?.free_capital)}
        {b.risk_budget_per_trade != null ? <> · risks up to {money(b.risk_budget_per_trade)} a trade ({+(b.risk_pct * 100).toFixed(2)}%)</> : null}
        {minLot(b.min_lot) ? <> · minimum size {minLot(b.min_lot)}</> : null} · {b.closed_trades} closed trade{b.closed_trades === 1 ? "" : "s"}</p>
      {bk?.shadow && <p className="muted small">Shadow mode: these are the amounts this bot would get once you arm.</p>}

      <h2>Where today&apos;s entry stops</h2>
      {roles.length ? <div className="pipelines">{roles.map((r) => <DecisionPipeline key={r} b={b} role={r} nowMin={nowMin} />)}</div>
        : <EmptyState>Waiting for the first bar to close.</EmptyState>}
      {f && <p className="funnel" style={{ marginTop: 12 }}><span className="muted">Last 7 days:</span>
        <span><b>{f.signals}</b> signals</span><span><b>{f.skipped_sizing}</b> skipped at sizing</span><span><b>{f.auditor_held}</b> held by auditor</span>
        <span><b>{f.risk_rejected}</b> refused by risk</span><span><b>{f.approved}</b> approved</span><span><b>{f.filled}</b> filled</span></p>}

      <h2>Last decision</h2>
      {sentence ? <div className="explain"><p>{sentence}</p><p className="tiny muted">{when(b.last_signal.ts)} · {b.last_signal.mode === "SHADOW" ? "shadow mode" : "armed"}</p></div>
        : <EmptyState>No signal yet. One appears when a completed bar meets every rule.</EmptyState>}

      <h2>Open trades</h2>
      {b.open_positions.length === 0 ? <EmptyState>Flat: no open trades.</EmptyState> : (
        <Table><thead><tr><th>Trade</th><th className="n">Entry</th><th>Stop ← → target</th><th>Opened</th></tr></thead>
          <tbody>{b.open_positions.map((p: Any) => <tr key={p.position_key}><td>{p.side === "LONG" ? "▲" : "▼"} {p.volume_open} {p.symbol}</td>
            <td className="n">{p.entry_price}</td><td><RangeBar p={p} /></td><td>{when(p.opened_at)}</td></tr>)}</tbody></Table>)}
      {b.open_positions.length > 0 && <p><Link href="/trades?tab=open">Manage on the Trades page →</Link></p>}

      <h2>Equity</h2>
      <EquityChart points={pts} markers={markers} />
      {markers.length > 0 && <p className="tiny muted">Dashed lines mark rule changes (new versions).</p>}
    </>
  );
}

/* ---------------------------------------------------------------- trades */
function BacktestTab({ id }: { id: string }) {
  const { data: runs } = usePoll<Any[]>("/api/backtests", 15000);
  const run = (runs ?? []).find((r) => r.strategy_id === id && r.status === "DONE");
  const { data: detail } = usePoll<Any>(run ? `/api/backtests/${run.id}` : "", 0);
  if (!runs) return <p className="empty">Loading…</p>;
  if (!run) return <p className="empty">No finished backtest for this bot yet. Run one on the Backtest page.</p>;
  if (!detail) return <p className="empty">Loading run {run.id}…</p>;
  const m = detail.metrics ?? {};
  return (
    <>
      <p className="muted">Latest backtest, run {run.id}: {m.n_trades} trades, {money(m.net_pnl, true)} net ({((m.total_return ?? 0) * 100).toFixed(1)}%).
        This is a replay on history, not demo trading.</p>
      <BacktestTrades trades={detail.trades ?? []} restarts={detail.diagnostics?.drawdown_restarts ?? []} />
    </>
  );
}

function TradesTab({ id }: { id: string }) {
  const [view, setView] = useState("demo");
  return (
    <>
      <Tabs tabs={[["demo", "Demo trades"], ["backtest", "Backtest trades"]]} value={view} onChange={setView} />
      {view === "demo" ? <DemoTrades id={id} /> : <BacktestTab id={id} />}
    </>
  );
}

function DemoTrades({ id }: { id: string }) {
  const { data } = usePoll<Any>(`/api/history?bot=${id}`, 10000);
  const rows: Any[] = data?.trades ?? [];
  if (!rows.length) return <EmptyState>No closed demo trades yet.</EmptyState>;
  return (
    <div className="table-wrap"><table><thead><tr><th>Closed</th><th>Trade</th><th>Held</th><th className="n">Gross</th><th className="n">Costs</th><th className="n">Net</th><th className="n">R</th><th>Rules</th><th>Why in / why out</th></tr></thead>
      <tbody>{rows.map((t) => <tr key={t.trade_id}><td>{when(t.exit_time)}{t.provisional ? "*" : ""}</td><td>{t.side === "LONG" ? "▲" : "▼"} {t.volume} {t.symbol}</td>
        <td>{minutes(t.minutes_held)}</td><td className="n">{money(t.gross, true)}</td><td className="n">{money(t.commission + t.swap + t.fee, true)}</td>
        <td className="n"><Pnl v={t.net} /></td><td className="n">{t.r_multiple ?? "–"}</td>
        <td className="muted tiny">{t.config_version ? `v${t.config_version}` : "–"}</td><td className="muted small">{t.entry_rationale} / {t.exit_rationale}</td></tr>)}</tbody></table></div>
  );
}

/* ---------------------------------------------------------------- review */
function GroupTable({ title, rows }: { title: string; rows: Any[] }) {
  if (!rows?.length) return null;
  return (
    <div className="panel">
      <h3>{title}</h3>
      <table><thead><tr><th /><th className="n">Trades</th><th className="n">Net</th><th className="n">Win</th><th className="n">Avg R</th></tr></thead>
        <tbody>{rows.map((g) => <tr key={g.key}><td>{g.key.replace(/_/g, " ").toLowerCase()}</td><td className="n">{g.n}</td>
          <td className="n"><Pnl v={g.net} /></td><td className="n">{g.win_rate}%</td><td className="n">{g.avg_r ?? "–"}</td></tr>)}</tbody></table>
    </div>
  );
}

function ReviewTab({ b, initialSource }: { b: Any; initialSource: string }) {
  const id = b.bot_id;
  const [source, setSource] = useState(initialSource || "demo");
  const { data: runs } = usePoll<Any[]>("/api/backtests", 15000);
  const { data: rv, error } = usePoll<Any>(`/api/review/${id}?source=${encodeURIComponent(source)}`, 0);
  const [tests, setTests] = useState<Record<string, Any>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const mine = (runs ?? []).filter((r) => r.strategy_id === id && r.status === "DONE");
  async function test(s: Any) {
    setMsg(null);
    try {
      const ds = source.startsWith("backtest:") && runs?.find((r) => `backtest:${r.id}` === source)?.dataset_key === "synthetic" ? "synthetic" : "mt5";
      const r = await api.post<Any>(`/api/review/${id}/test`, { change: s.change, dataset: ds });
      setTests({ ...tests, [s.id]: r });
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  async function apply(s: Any) {
    const name = await confirmAction({ title: `Apply this change to ${CREATURES[id]?.name}?`, confirmLabel: "Apply",
      consequences: [Object.entries(s.change).map(([k, v]) => `${k} → ${v}`).join(", "), `Saved as rules v${(b.config_version ?? 1) + 1}; the change is logged.`] });
    if (!name) return;
    try {
      const params = Object.fromEntries(Object.entries(b.strategy.params).map(([k, p]: [string, Any]) => [k, p.value]));
      await api.put(`/api/bots/${id}/params`, { params: { ...params, ...s.change }, operator: name });
      setMsg(`Applied as rules v${(b.config_version ?? 1) + 1}.`);
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  const st = rv?.stats;
  return (
    <>
      <div className="row-between">
        <p className="muted">The auditor reviews how this bot&apos;s trades turned out and suggests changes you can test on history before applying. It never changes a bot by itself.</p>
        <label>Trades to review
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="demo">Demo trades</option>
            {mine.map((r) => <option key={r.id} value={`backtest:${r.id}`}>Backtest #{r.id} ({r.dataset_key === "synthetic" ? "synthetic" : "MT5 history"}, {r.metrics?.n_trades} trades)</option>)}
          </select>
        </label>
      </div>
      {error && <div className="notice notice-danger">{error}</div>}
      {msg && <div className="notice">{msg}</div>}
      {!rv ? <p className="empty">Reviewing…</p> : <>
        <h2>Suggestions</h2>
        <div className="suggestions">
          {rv.suggestions.map((s: Any) => (
            <div key={s.id} className={`suggestion k-${s.kind}`}>
              <div className="row-between"><b>{s.title}</b>{s.confidence && <Pill>confidence: {s.confidence}</Pill>}</div>
              <p className="muted">{s.evidence}</p>
              {s.change && <p>Proposed change: <code>{Object.entries(s.change).map(([k, v]) => `${k} → ${v}`).join(", ")}</code></p>}
              {s.rule_change && <p>Needs a rule change: <i>{s.rule_change}</i> (ask for it to be coded and tested).</p>}
              {s.change && (
                <div className="actions">
                  <Button size="sm" onClick={() => test(s)}>Test on history</Button>
                  <Button size="sm" onClick={() => apply(s)} disabled={b.status === "RUNNING" || b.open_positions.length > 0}
                          title={b.status === "RUNNING" ? "Pause the bot first" : ""}>Apply</Button>
                </div>
              )}
              {tests[s.id] && <Compare2 ids={tests[s.id]} />}
            </div>
          ))}
        </div>
        {st?.n > 0 && <>
          <h2>Outcome review <span className="muted small">({rv.source})</span></h2>
          <Stats>
            <Metric k="Trades" v={String(st.n)} />
            <Metric k="Net" v={<Pnl v={st.net} />} />
            <Metric k="Win rate" v={`${st.win_rate}%`} />
            <Metric k="Avg win / loss" v={`${money(st.avg_win)} / ${money(st.avg_loss)}`} />
            <Metric k="Per trade" v={`${money(st.expectancy, true)}${st.expectancy_r != null ? ` (${st.expectancy_r}R)` : ""}`} />
            <Metric k="Profit factor" v={String(st.profit_factor ?? "–")} />
            <Metric k="Costs share of gross" v={`${st.cost_share_pct}%`} />
            <Metric k="Avg time win / loss" v={`${minutes(st.avg_minutes_win)} / ${minutes(st.avg_minutes_loss)}`} />
          </Stats>
          {st.excursions && (
            <Stats>
              <Metric k="Stopped after being +1R" v={st.excursions.stopped_after_reaching_1r_pct != null ? `${st.excursions.stopped_after_reaching_1r_pct}%` : "–"} />
              <Metric k="Winners that nearly hit the stop" v={st.excursions.winners_near_stop_pct != null ? `${st.excursions.winners_near_stop_pct}%` : "–"} />
              <Metric k="Losers' best point" v={st.excursions.avg_mfe_r_losers != null ? `${st.excursions.avg_mfe_r_losers}R` : "–"} />
              <Metric k="Winners' worst point" v={st.excursions.avg_mae_r_winners != null ? `${st.excursions.avg_mae_r_winners}R` : "–"} />
            </Stats>
          )}
          <div className="review-grid">
            <GroupTable title="By exit" rows={st.by_exit} />
            <GroupTable title="By side" rows={st.by_side} />
            <GroupTable title="By metal" rows={st.by_metal} />
            <GroupTable title="By session (UTC)" rows={st.by_session} />
            <GroupTable title="By weekday" rows={st.by_weekday} />
          </div>
        </>}
      </>}
    </>
  );
}

/* ------------------------------------------------------------ settings */
function ParamsForm({ bot, onDone }: { bot: Any; onDone: () => void }) {
  const params = bot.strategy.params as Record<string, Any>;
  const [vals, setVals] = useState<Record<string, string>>({});
  useEffect(() => setVals(Object.fromEntries(Object.entries(params).map(([k, p]) => [k, String(p.value)]))), [bot.config_version]); // eslint-disable-line react-hooks/exhaustive-deps
  const [msg, setMsg] = useState<string | null>(null);
  const locked = bot.status === "RUNNING" || bot.open_positions.length > 0;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const name = await confirmAction({ title: `Save new parameters for ${CREATURES[bot.bot_id]?.name}?`, confirmLabel: "Save",
      consequences: [`They become rules v${(bot.config_version ?? 1) + 1}; trades from now on are marked with it.`, "The change is logged."] });
    if (!name) return;
    const out: Record<string, Any> = {};
    for (const [k, p] of Object.entries(params)) {
      const raw = vals[k];
      out[k] = typeof p.default === "number" ? Number(raw) : raw === "null" ? null : raw;
    }
    try { await api.put(`/api/bots/${bot.bot_id}/params`, { params: out, operator: name }); setMsg(`Saved as rules v${(bot.config_version ?? 1) + 1}.`); onDone(); }
    catch (err) { setMsg(err instanceof Error ? err.message : String(err)); }
  }
  return (
    <form onSubmit={save}>
      {locked && <div className="notice">Pause the bot and close its trades to change parameters.</div>}
      <div className="table-wrap"><table>
        <thead><tr><th>Parameter</th><th>Value</th><th>Default</th><th>Why</th></tr></thead>
        <tbody>{Object.entries(params).map(([k, p]) => (
          <tr key={k}><td>{k}</td>
            <td><input aria-label={k} value={vals[k] ?? ""} disabled={locked} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} size={8} /></td>
            <td className="muted">{String(p.default)}</td><td className="muted small">{p.rationale}</td></tr>))}</tbody>
      </table></div>
      <div className="actions" style={{ marginTop: 10 }}>
        <Button tone="primary" disabled={locked}>Save parameters</Button>
        {msg && <span className="muted">{msg}</span>}
      </div>
    </form>
  );
}

function RulesTab({ b, reload }: { b: Any; reload: () => void }) {
  const st = b.strategy;
  return (
    <>
      <dl className="rules">
        <dt>Long</dt><dd>{st.long_rules}</dd><dt>Short</dt><dd>{st.short_rules}</dd><dt>Exits, stops, targets</dt><dd>{st.exit_rules}</dd>
        <dt>Invalidation</dt><dd>{st.invalidation_rules}</dd><dt>Filters</dt><dd>{st.filters}</dd><dt>Known limitations</dt><dd>{st.limitations}</dd>
        <dt>Sources</dt><dd><ul>{st.citations.map((c: string) => <li key={c}>{c}</li>)}</ul></dd>
      </dl>
      <h2>Parameters</h2>
      <ParamsForm bot={b} onDone={reload} />
    </>
  );
}

/* ---------------------------------------------------------------- page */
/** S starts and P pauses the bot whose page this is (each through the confirm dialog). */
function useBotShortcuts(b: Any) {
  useEffect(() => {
    if (!b) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [role=dialog], [role=alertdialog]") || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "s" && b.status !== "RUNNING" && b.status !== "RETIRED") { e.preventDefault(); botAction(b.bot_id, "start").catch(() => {}); }
      if (k === "p" && b.status === "RUNNING") { e.preventDefault(); botAction(b.bot_id, "pause", b.open_positions.length).catch(() => {}); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [b]);
}
function BotDetail({ id }: { id: string }) {
  const sp = useSearchParams();
  const { data: b, reload, error } = usePoll<Any>(`/api/bots/${id}`, 4000);
  const sum = useSummary();
  const [tab, setTab] = useState(sp.get("tab") || "live");
  useBotShortcuts(b);
  if (error) return <div className="notice notice-danger">{error}</div>;
  if (!b) return <p className="empty">Loading…</p>;
  const st = b.strategy;
  const mood = b.status === "RUNNING" ? (b.open_positions.length ? "busy" : "idle") : b.status === "PAUSED_RISK" ? "hurt" : b.status === "RETIRED" ? "asleep" : "still";
  const side = id.endsWith("L") ? "▲ Long bot" : "▼ Short bot";
  const who = CHARACTERS[id];
  const row = sum?.bots?.find((x: Any) => x.bot_id === id);
  const t = sum?.server_time ? new Date(sum.server_time) : new Date();
  const nowMin = t.getUTCHours() * 60 + t.getUTCMinutes();
  return (
    <>
      <p className="crumbs"><Link href="/">← All bots</Link></p>
      <div className="hero">
        <div className="hero-art"><Creature bot={id} size={190} mood={mood} scene /><StateSymbol state={b.status === "RETIRED" ? "retired" : stateSymbol(b.status, !!sum?.kill)} /></div>
        <div className="hero-text">
          <h1 className="hero-title">{CREATURES[id]?.name} <SideIcon bot={id} size={52} /></h1>
          <p><b>{id} {st.name}</b> · {side} · {st.timeframes.join(" + ")} · {b.symbols.join(" and ") || st.roles.join(" and ")}{" "}
            <Pill tone="info" title={b.rule_changes?.length ? `Last changed ${when(b.rule_changes.at(-1).at)}` : "Unchanged since the bot was created"}>Rules v{b.config_version}</Pill>{" "}
            <Pill tone="info" title="Every bot trades the demo account">Demo</Pill></p>
          {st.trading_window && <p className="window-line"><b>Can open trades today:</b> {st.trading_window.effective_utc}
            <span className="muted"> (its own session: {st.trading_window.built_in}; hour filter: {st.trading_window.filter_utc === "all" ? "none" : `${st.trading_window.filter_utc} UTC`})</span></p>}
          {who && <p className="character"><b>Looks:</b> {who.looks}<br /><b>Personality:</b> {who.personality}</p>}
          <p className="muted">{st.description}</p>
          <div className="actions"><StatusBadge status={b.status} extra={b.pause_reason} />
            <BotControls bot={id} status={b.status} hasOpen={b.open_positions.length > 0} openTrades={b.open_positions.length} onDone={reload} retire kill={!!sum?.kill} /></div>
        </div>
      </div>
      <Tabs tabs={[["live", "Live"], ["trades", "Trades"], ["review", "Review"], ["rules", "Rules & settings"]]} value={tab} onChange={setTab} />
      {tab === "live" && <LiveTab b={b} nowMin={nowMin} todayPnl={row?.today_pnl} />}
      {tab === "trades" && <TradesTab id={id} />}
      {tab === "review" && <ReviewTab b={b} initialSource={sp.get("source") || "demo"} />}
      {tab === "rules" && <RulesTab b={b} reload={reload} />}
    </>
  );
}

function Inner() {
  const id = useSearchParams().get("id");
  if (!id || !CREATURES[id]) return <p className="empty">Pick a bot in the <Link href="/">Lab</Link>.</p>;
  return <BotDetail id={id} />;
}

export default function BotPage() {
  return <Suspense fallback={<p className="empty">Loading…</p>}><Inner /></Suspense>;
}
