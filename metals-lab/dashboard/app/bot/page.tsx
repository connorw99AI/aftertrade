"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Creature, CREATURES } from "@/components/Creature";
import { RoleThinking } from "@/components/Thinking";
import { Any, api, money, pct, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

function Metric({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return <div className="metric"><span>{k}</span><b className={tone}>{v}</b></div>;
}

function BotList() {
  const { data } = usePoll<Any[]>("/api/bots", 10000);
  return (
    <>
      <h1>Bots</h1>
      <div className="table-wrap"><table>
        <thead><tr><th>Creature</th><th>Strategy</th><th>Style</th><th>Status</th><th className="n">Equity</th><th className="n">Closed P&amp;L</th><th className="n">Trades</th></tr></thead>
        <tbody>{data?.map((b) => (
          <tr key={b.bot_id}>
            <td><Link href={`/bot?id=${b.bot_id}`}>{CREATURES[b.bot_id]?.name}</Link></td><td>{b.bot_id} {b.name}</td>
            <td>{b.style.toLowerCase()}</td><td>{b.status}</td><td className="n">{b.book ? money(b.book.equity) : "–"}</td>
            <td className="n">{money(b.closed_net_pnl, true)}</td><td className="n">{b.closed_trades}</td>
          </tr>))}</tbody>
      </table></div>
    </>
  );
}

function ParamsForm({ bot, onDone }: { bot: Any; onDone: () => void }) {
  const params = bot.strategy.params as Record<string, Any>;
  const [vals, setVals] = useState<Record<string, string>>(Object.fromEntries(Object.entries(params).map(([k, p]) => [k, String(p.value)])));
  const [operator, setOperator] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const locked = bot.status === "RUNNING" || bot.open_positions.length > 0;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const out: Record<string, Any> = {};
    for (const [k, p] of Object.entries(params)) {
      const raw = vals[k];
      out[k] = typeof p.default === "number" ? Number(raw) : raw === "null" ? null : raw;
    }
    try {
      await api.put(`/api/bots/${bot.bot_id}/params`, { params: out, operator });
      setMsg("Saved as a new configuration version.");
      onDone();
    } catch (err) { setMsg(err instanceof Error ? err.message : String(err)); }
  }
  return (
    <form onSubmit={save}>
      {locked && <div className="warn">Pause the bot and close its positions to change parameters. Defaults are research hypotheses, not tuned values.</div>}
      <div className="table-wrap"><table>
        <thead><tr><th>Parameter</th><th>Value</th><th>Default</th><th>Why</th></tr></thead>
        <tbody>{Object.entries(params).map(([k, p]) => (
          <tr key={k}><td>{k}</td>
            <td><input aria-label={k} value={vals[k]} disabled={locked} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} size={8} /></td>
            <td className="muted">{String(p.default)}</td><td className="muted">{p.rationale}</td></tr>))}</tbody>
      </table></div>
      <div className="actions" style={{ marginTop: 10 }}>
        <label>Your name (audit record)<input value={operator} onChange={(e) => setOperator(e.target.value)} required disabled={locked} /></label>
        <button className="primary" disabled={locked || !operator}>Save parameters</button>
        {msg && <span className="muted">{msg}</span>}
      </div>
    </form>
  );
}

function BotDetail({ id }: { id: string }) {
  const { data: b, reload, error } = usePoll<Any>(`/api/bots/${id}`, 5000);
  const { data: trades } = usePoll<Any[]>(`/api/bots/${id}/trades`, 10000);
  const { data: signals } = usePoll<Any[]>(`/api/bots/${id}/signals?limit=20`, 10000);
  const { data: decisions } = usePoll<Any>(`/api/bots/${id}/decisions?limit=20`, 10000);
  const [msg, setMsg] = useState<string | null>(null);
  if (error) return <div className="warn danger">{error}</div>;
  if (!b) return <p className="empty">Loading…</p>;
  const st = b.strategy;
  async function act(a: string) {
    try { await api.post(`/api/bots/${id}/${a}`, { reason: `dashboard ${a}` }); setMsg(null); reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  const bk = b.book;
  return (
    <>
      <div className="hero">
        <Creature bot={id} size={120} mood={b.status === "RUNNING" ? "idle" : b.status === "PAUSED_RISK" ? "hurt" : "asleep"} />
        <div>
          <h1>{CREATURES[id]?.name}</h1>
          <p>{id} {st.name}: {st.description}</p>
          <p className="muted">{st.style.toLowerCase()} on {b.symbols.join(" and ") || st.roles.join(" and ")}, {st.timeframes.join("/")} bars, magic {b.magic}, config v{b.config_version}</p>
          <div className="actions">
            <span className={`badge ${b.status}`}>{b.status}</span>
            <button className="primary" onClick={() => act("start")} disabled={b.status === "RUNNING"}>Start</button>
            <button onClick={() => act("pause")} disabled={b.status !== "RUNNING"}>Pause new entries</button>
            <button onClick={() => act("stop")}>Stop</button>
            {msg && <span className="loss">{msg}</span>}
          </div>
          {b.pause_reason && <p className="muted">Reason: {b.pause_reason}</p>}
        </div>
      </div>
      <h2>Own money</h2>
      {bk?.shadow && <p className="muted">Shadow mode: these are the amounts this bot would get when you arm (10% of the current balance). No money is assigned yet.</p>}
      {!bk || !bk.allocation ? <p className="empty">No allocation yet. Allocations are created on first arming.</p> : (
        <div className="metrics">
          <Metric k="Starting allocation" v={money(bk.allocation)} />
          <Metric k="Equity" v={money(bk.equity)} />
          <Metric k="Realized (net of costs)" v={money(bk.realized, true)} tone={bk.realized >= 0 ? "gain" : "loss"} />
          <Metric k="Unrealized" v={money(bk.unrealized, true)} />
          <Metric k="Reserved margin" v={money(bk.reserved_margin)} />
          <Metric k="Free capital" v={money(bk.free_capital)} />
          <Metric k={`Sizing target (${(b.risk_pct * 100).toFixed(1)}%)`} v={money(b.risk_budget_per_trade)} />
          {minLot(b.min_lot) && <Metric k="Minimum trade size" v={minLot(b.min_lot)!} />}
          {b.max_entries_per_day > 0 && <Metric k="Trades today (UTC)" v={`${b.entries_today ?? 0} / ${b.max_entries_per_day}`} />}
          <Metric k="Return" v={pct(b.return_pct)} />
          <Metric k="Max drawdown (closed)" v={pct(b.max_drawdown_closed_pct)} />
          <Metric k="Closed trades" v={String(b.closed_trades)} />
        </div>)}
      {b.sample_size_note && <p className="muted">{b.sample_size_note}</p>}
      {minLot(b.min_lot) && !b.per_trade_limit_enabled && <p className="muted">Every trade is at least {minLot(b.min_lot)} and there is no per-trade loss cap: a single stop-out can lose more than the sizing target. Only the daily loss stop limits losses, and it acts after the loss.</p>}

      <h2>What it sees now</h2>
      {b.thinking ? (
        <div className="thinking-list">
          {Object.keys(b.thinking.roles).map((role) => (
            <div key={role}><h3>{role === "GOLD" ? "Gold" : "Silver"}</h3><RoleThinking bot={id} th={b.thinking} role={role} /></div>
          ))}
        </div>
      ) : <p className="empty">The checklist appears after the next {st.primary_timeframe} bar closes.</p>}
      <h3>Last signal</h3>
      {b.last_signal ? (
        <div className="explain">
          <p>{b.last_signal.explanation}</p>
          <p className="muted">Outcome: {b.last_signal.outcome} ({b.last_signal.mode.toLowerCase()}), {when(b.last_signal.ts)}</p>
        </div>) : <p className="empty">No signal yet. Signals appear when a completed bar meets the rules below.</p>}
      <div className="grid2" style={{ marginTop: 12 }}>
        <div className="panel"><h3>Last auditor decision</h3>{b.last_audit ? <>
          <p><b>{b.last_audit.decision}</b> on {b.last_audit.symbol} {b.last_audit.intent}</p>
          <p className="muted">{JSON.parse(b.last_audit.reasons_json || "[]").join("; ") || "No rule triggered."} News: {b.last_audit.news_status}. Ruleset {b.last_audit.ruleset_version}.</p></>
          : <p className="empty">None yet.</p>}</div>
        <div className="panel"><h3>Last risk decision</h3>{b.last_risk ? <>
          <p><b>{b.last_risk.decision}</b> on {b.last_risk.symbol} {b.last_risk.intent}</p>
          <p className="muted">{JSON.parse(b.last_risk.reasons_json || "[]").join("; ") || "All limits passed."}</p></>
          : <p className="empty">None yet.</p>}</div>
      </div>

      <h2>Open positions</h2>
      {b.open_positions.length === 0 ? <p className="empty">Flat.</p> : (
        <div className="table-wrap"><table><thead><tr><th>Ticket</th><th>Symbol</th><th>Side</th><th className="n">Volume</th><th className="n">Entry</th><th className="n">Stop</th><th className="n">Target</th><th className="n">Margin</th><th>Opened</th></tr></thead>
          <tbody>{b.open_positions.map((p: Any) => <tr key={p.position_key}><td>{p.ticket}</td><td>{p.symbol}</td><td>{p.side}</td><td className="n">{p.volume_open}</td><td className="n">{p.entry_price}</td><td className="n">{p.sl ?? "–"}</td><td className="n">{p.tp ?? "–"}</td><td className="n">{money(p.reserved_margin)}</td><td>{when(p.opened_at)}</td></tr>)}</tbody></table></div>)}

      <h2>Closed trades</h2>
      {!trades?.length ? <p className="empty">No closed forward-demo trades yet.</p> : (
        <div className="table-wrap"><table><thead><tr><th>Closed</th><th>Symbol</th><th>Side</th><th className="n">Gross</th><th className="n">Costs</th><th className="n">Net</th><th>Why in / why out</th></tr></thead>
          <tbody>{trades.map((t) => <tr key={t.trade_id}><td>{when(t.exit_time)}{t.provisional ? " (provisional)" : ""}</td><td>{t.symbol}</td><td>{t.side}</td>
            <td className="n">{money(t.gross, true)}</td><td className="n">{money(t.commission + t.swap + t.fee, true)}</td>
            <td className={`n ${t.net >= 0 ? "gain" : "loss"}`}>{money(t.net, true)}</td><td className="muted">{t.entry_rationale} / {t.exit_rationale}</td></tr>)}</tbody></table></div>)}

      <h2>Rules</h2>
      <dl className="rules">
        <dt>Long</dt><dd>{st.long_rules}</dd><dt>Short</dt><dd>{st.short_rules}</dd><dt>Exits, stops, targets</dt><dd>{st.exit_rules}</dd>
        <dt>Invalidation</dt><dd>{st.invalidation_rules}</dd><dt>Filters</dt><dd>{st.filters}</dd><dt>Known limitations</dt><dd>{st.limitations}</dd>
        <dt>Sources</dt><dd><ul>{st.citations.map((c: string) => <li key={c}>{c}</li>)}</ul></dd>
      </dl>
      <h2>Parameters</h2>
      <ParamsForm bot={b} onDone={reload} />

      <h2>Recent signals</h2>
      {!signals?.length ? <p className="empty">None recorded.</p> : (
        <div className="table-wrap"><table><thead><tr><th>Time</th><th>Mode</th><th>Outcome</th><th>Explanation</th></tr></thead>
          <tbody>{signals.map((s) => <tr key={s.id}><td>{when(s.ts)}</td><td>{s.mode}</td><td>{s.outcome}</td><td className="muted">{s.explanation}</td></tr>)}</tbody></table></div>)}
      <details style={{ marginTop: 12 }}><summary>Raw auditor and risk decisions</summary><pre>{JSON.stringify(decisions, null, 1)}</pre></details>
    </>
  );
}

function Inner() {
  const id = useSearchParams().get("id");
  return id ? <BotDetail id={id} /> : <BotList />;
}

function minLot(v: Any): string | null {
  if (v && typeof v === "object") {
    const parts = Object.entries(v).filter(([, x]) => Number(x) > 0).map(([k, x]) => `${k === "GOLD" ? "gold" : "silver"} ${x}`);
    return parts.length ? `${parts.join(", ")} lot` : null;
  }
  return Number(v) > 0 ? `${v} lot` : null;
}

export default function BotPage() {
  return <Suspense fallback={<p className="empty">Loading…</p>}><Inner /></Suspense>;
}
