"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { BotControls, StatusBadge, Tabs } from "@/components/Bits";
import { Creature, CREATURES } from "@/components/Creature";
import { RoleThinking } from "@/components/Thinking";
import { Any, api, confirmAction, minutes, money, pct, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

function Metric({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return <div className="metric"><span>{k}</span><b className={tone}>{v}</b></div>;
}

function minLot(v: Any): string | null {
  if (v && typeof v === "object") {
    const parts = Object.entries(v).filter(([, x]) => Number(x) > 0).map(([k, x]) => `${k === "GOLD" ? "gold" : "silver"} ${x}`);
    return parts.length ? `${parts.join(", ")} lot` : null;
  }
  return Number(v) > 0 ? `${v} lot` : null;
}

/* ------------------------------------------------------------------ live */
function LiveTab({ b }: { b: Any }) {
  const bk = b.book;
  return (
    <>
      <div className="metrics">
        <Metric k="Equity" v={money(bk?.equity)} />
        <Metric k="Starting allocation" v={money(bk?.allocation)} />
        <Metric k="Realized (net)" v={money(bk?.realized, true)} tone={(bk?.realized ?? 0) >= 0 ? "gain" : "loss"} />
        <Metric k="Open P&L" v={money(bk?.unrealized, true)} tone={(bk?.unrealized ?? 0) >= 0 ? "gain" : "loss"} />
        <Metric k="Free capital" v={money(bk?.free_capital)} />
        <Metric k="Trades today" v={`${b.entries_today ?? 0}${b.max_entries_per_day ? ` / ${b.max_entries_per_day}` : ""}`} />
        <Metric k="Return" v={pct(b.return_pct)} />
        {minLot(b.min_lot) && <Metric k="Minimum trade size" v={minLot(b.min_lot)!} />}
      </div>
      {bk?.shadow && <p className="muted">Shadow mode: these are the amounts this bot would get once you arm.</p>}
      <h2>What it sees now</h2>
      {b.thinking ? (
        <div className="thinking-list">
          {Object.keys(b.thinking.roles).map((role) => (
            <div key={role}><h3>{role === "GOLD" ? "Gold" : "Silver"}</h3><RoleThinking bot={b.bot_id} th={b.thinking} role={role} /></div>
          ))}
        </div>
      ) : <p className="empty">Waiting for the first bar to close…</p>}
      <h2>Open trades</h2>
      {b.open_positions.length === 0 ? <p className="empty">Flat.</p> : (
        <div className="table-wrap"><table><thead><tr><th>Trade</th><th className="n">Entry</th><th className="n">Stop</th><th className="n">Target</th><th>Opened</th></tr></thead>
          <tbody>{b.open_positions.map((p: Any) => <tr key={p.position_key}><td>{p.side === "LONG" ? "▲" : "▼"} {p.volume_open} {p.symbol}</td>
            <td className="n">{p.entry_price}</td><td className="n">{p.sl ?? "none"}</td><td className="n">{p.tp ?? "none"}</td><td>{when(p.opened_at)}</td></tr>)}</tbody></table>
          <p><Link href="/positions">Manage on the Positions page →</Link></p></div>)}
      <h2>Last decision</h2>
      {b.last_signal ? (
        <div className="explain">
          <p>{b.last_signal.explanation}</p>
          <p className="muted">Outcome: {b.last_signal.outcome} ({b.last_signal.mode.toLowerCase()}), {when(b.last_signal.ts)}</p>
          {b.last_audit && <p className="muted">Auditor: {b.last_audit.decision}. {JSON.parse(b.last_audit.reasons_json || "[]").join("; ")}</p>}
          {b.last_risk && <p className="muted">Risk: {b.last_risk.decision}. {JSON.parse(b.last_risk.reasons_json || "[]").join("; ")}</p>}
        </div>) : <p className="empty">No signal yet. Signals appear when a completed bar meets the rules.</p>}
      <p><Link href={`/activity`}>Full decision feed →</Link></p>
    </>
  );
}

/* ---------------------------------------------------------------- trades */
function TradesTab({ id }: { id: string }) {
  const { data } = usePoll<Any>(`/api/history?bot=${id}`, 10000);
  const rows: Any[] = data?.trades ?? [];
  if (!rows.length) return <p className="empty">No closed demo trades yet.</p>;
  return (
    <div className="table-wrap"><table><thead><tr><th>Closed</th><th>Trade</th><th>Held</th><th className="n">Gross</th><th className="n">Costs</th><th className="n">Net</th><th className="n">R</th><th>Why in / why out</th></tr></thead>
      <tbody>{rows.map((t) => <tr key={t.trade_id}><td>{when(t.exit_time)}{t.provisional ? "*" : ""}</td><td>{t.side === "LONG" ? "▲" : "▼"} {t.volume} {t.symbol}</td>
        <td>{minutes(t.minutes_held)}</td><td className="n">{money(t.gross, true)}</td><td className="n">{money(t.commission + t.swap + t.fee, true)}</td>
        <td className={`n ${t.net >= 0 ? "gain" : "loss"}`}>{money(t.net, true)}</td><td className="n">{t.r_multiple ?? "–"}</td>
        <td className="muted small">{t.entry_rationale} / {t.exit_rationale}</td></tr>)}</tbody></table></div>
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
          <td className={`n ${g.net >= 0 ? "gain" : "loss"}`}>{money(g.net, true)}</td><td className="n">{g.win_rate}%</td><td className="n">{g.avg_r ?? "–"}</td></tr>)}</tbody></table>
    </div>
  );
}

function Compare2({ ids }: { ids: { baseline_id: number; candidate_id: number; change: Any } }) {
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
    const name = confirmAction(`Apply ${JSON.stringify(s.change)} to ${CREATURES[id]?.name}? The bot must be paused and flat; this is logged.`);
    if (!name) return;
    try {
      const params = Object.fromEntries(Object.entries(b.strategy.params).map(([k, p]: [string, Any]) => [k, p.value]));
      await api.put(`/api/bots/${id}/params`, { params: { ...params, ...s.change }, operator: name });
      setMsg("Applied as a new configuration version.");
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
      {error && <div className="warn danger">{error}</div>}
      {msg && <div className="warn">{msg}</div>}
      {!rv ? <p className="empty">Reviewing…</p> : <>
        <h2>Suggestions</h2>
        <div className="suggestions">
          {rv.suggestions.map((s: Any) => (
            <div key={s.id} className={`suggestion k-${s.kind}`}>
              <div className="row-between"><b>{s.title}</b>{s.confidence && <span className="badge">confidence: {s.confidence}</span>}</div>
              <p className="muted">{s.evidence}</p>
              {s.change && <p>Proposed change: <code>{Object.entries(s.change).map(([k, v]) => `${k} → ${v}`).join(", ")}</code></p>}
              {s.rule_change && <p>Needs a rule change: <i>{s.rule_change}</i> (ask for it to be coded and tested).</p>}
              {s.change && (
                <div className="actions">
                  <button onClick={() => test(s)}>Test on history</button>
                  <button onClick={() => apply(s)} disabled={b.status === "RUNNING" || b.open_positions.length > 0}
                          title={b.status === "RUNNING" ? "Pause the bot first" : ""}>Apply</button>
                </div>
              )}
              {tests[s.id] && <Compare2 ids={tests[s.id]} />}
            </div>
          ))}
        </div>
        {st?.n > 0 && <>
          <h2>Outcome review <span className="muted small">({rv.source})</span></h2>
          <div className="metrics">
            <Metric k="Trades" v={String(st.n)} />
            <Metric k="Net" v={money(st.net, true)} tone={st.net >= 0 ? "gain" : "loss"} />
            <Metric k="Win rate" v={`${st.win_rate}%`} />
            <Metric k="Avg win / loss" v={`${money(st.avg_win)} / ${money(st.avg_loss)}`} />
            <Metric k="Per trade" v={`${money(st.expectancy, true)}${st.expectancy_r != null ? ` (${st.expectancy_r}R)` : ""}`} />
            <Metric k="Profit factor" v={String(st.profit_factor ?? "–")} />
            <Metric k="Costs share of gross" v={`${st.cost_share_pct}%`} />
            <Metric k="Avg time win / loss" v={`${minutes(st.avg_minutes_win)} / ${minutes(st.avg_minutes_loss)}`} />
          </div>
          {st.excursions && (
            <div className="metrics">
              <Metric k="Stopped after being +1R" v={st.excursions.stopped_after_reaching_1r_pct != null ? `${st.excursions.stopped_after_reaching_1r_pct}%` : "–"} />
              <Metric k="Winners that nearly hit the stop" v={st.excursions.winners_near_stop_pct != null ? `${st.excursions.winners_near_stop_pct}%` : "–"} />
              <Metric k="Losers' best point" v={st.excursions.avg_mfe_r_losers != null ? `${st.excursions.avg_mfe_r_losers}R` : "–"} />
              <Metric k="Winners' worst point" v={st.excursions.avg_mae_r_winners != null ? `${st.excursions.avg_mae_r_winners}R` : "–"} />
            </div>
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
    const name = confirmAction(`Save new parameters for ${CREATURES[bot.bot_id]?.name}?`);
    if (!name) return;
    const out: Record<string, Any> = {};
    for (const [k, p] of Object.entries(params)) {
      const raw = vals[k];
      out[k] = typeof p.default === "number" ? Number(raw) : raw === "null" ? null : raw;
    }
    try { await api.put(`/api/bots/${bot.bot_id}/params`, { params: out, operator: name }); setMsg("Saved as a new configuration version."); onDone(); }
    catch (err) { setMsg(err instanceof Error ? err.message : String(err)); }
  }
  return (
    <form onSubmit={save}>
      {locked && <div className="warn">Pause the bot and close its trades to change parameters.</div>}
      <div className="table-wrap"><table>
        <thead><tr><th>Parameter</th><th>Value</th><th>Default</th><th>Why</th></tr></thead>
        <tbody>{Object.entries(params).map(([k, p]) => (
          <tr key={k}><td>{k}</td>
            <td><input aria-label={k} value={vals[k] ?? ""} disabled={locked} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} size={8} /></td>
            <td className="muted">{String(p.default)}</td><td className="muted small">{p.rationale}</td></tr>))}</tbody>
      </table></div>
      <div className="actions" style={{ marginTop: 10 }}>
        <button className="primary" disabled={locked}>Save parameters</button>
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
function BotDetail({ id }: { id: string }) {
  const sp = useSearchParams();
  const { data: b, reload, error } = usePoll<Any>(`/api/bots/${id}`, 4000);
  const [tab, setTab] = useState(sp.get("tab") || "live");
  if (error) return <div className="warn danger">{error}</div>;
  if (!b) return <p className="empty">Loading…</p>;
  const st = b.strategy;
  const mood = b.status === "RUNNING" ? (b.open_positions.length ? "busy" : "idle") : b.status === "PAUSED_RISK" ? "hurt" : "asleep";
  return (
    <>
      <p className="crumbs"><Link href="/">← All bots</Link></p>
      <div className="hero">
        <div className="hero-art"><Creature bot={id} size={168} mood={mood} /></div>
        <div className="hero-text">
          <h1>{CREATURES[id]?.name}</h1>
          <p><b>{id} {st.name}</b> · {st.timeframes.join(" + ")} · {b.symbols.join(" and ") || st.roles.join(" and ")}</p>
          <p className="muted">{st.description}</p>
          <div className="actions"><StatusBadge status={b.status} extra={b.pause_reason} />
            <BotControls bot={id} status={b.status} hasOpen={b.open_positions.length > 0} onDone={reload} /></div>
        </div>
      </div>
      <Tabs tabs={[["live", "Live"], ["trades", "Trades"], ["review", "Review"], ["rules", "Rules & settings"]]} value={tab} onChange={setTab} />
      {tab === "live" && <LiveTab b={b} />}
      {tab === "trades" && <TradesTab id={id} />}
      {tab === "review" && <ReviewTab b={b} initialSource={sp.get("source") || "demo"} />}
      {tab === "rules" && <RulesTab b={b} reload={reload} />}
    </>
  );
}

function Inner() {
  const id = useSearchParams().get("id");
  if (!id || !CREATURES[id]) return <p className="empty">Pick a bot on the <Link href="/">Overview</Link>.</p>;
  return <BotDetail id={id} />;
}

export default function BotPage() {
  return <Suspense fallback={<p className="empty">Loading…</p>}><Inner /></Suspense>;
}
