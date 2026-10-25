"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { botAction, StatusBadge, Tabs } from "@/components/Bits";
import { Creature, CREATURES } from "@/components/Creature";
import { AppliedSuggestion, applySuggestion, changeText, TestNumbers, TestStatus, testNow } from "@/components/Suggestion";
import { Button, domainOf, EmptyState, Pill, Pnl, ProgressBar, Sparkline, Table } from "@/components/ui";
import { Any, api, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { blockDetail, capitalise, reasonText } from "@/lib/wording";

const GRADE_CLASS: Record<string, string> = { keep: "grade-keep", retire: "grade-retire", watch: "grade-watch", "too early": "grade-early" };

/** Until there is enough data the grade is a progress bar toward judgement; then the grade itself. */
function Grade({ g, label }: { g: Any; label: string }) {
  const m = String(g?.why ?? "").match(/(\d+) of (\d+) trades/);
  if (g?.grade === "too early" && m) return <div className="grade"><span className="muted tiny">{label}</span><ProgressBar value={Number(m[1])} target={Number(m[2])} label="trades" /></div>;
  return <div className="grade"><span className="muted tiny">{label}</span><b className={GRADE_CLASS[g?.grade] ?? "muted"}>{g?.grade ?? "–"}</b><span className="tiny muted">{g?.why}</span></div>;
}

function Grades({ data, reload }: { data: Any; reload: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const cards: Any[] = data?.cards ?? [];
  async function retire(bot: string) {
    try { if (await botAction(bot, "retire")) { setMsg(`${CREATURES[bot]?.name} retired; it keeps its balance.`); reload(); } }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  const card = (c: Any) => (
    <div key={c.bot_id} className={`report-card ${c.status === "RETIRED" ? "card-retired" : ""}`}>
      <Link href={`/bot?id=${c.bot_id}&tab=review`} className="who"><Creature bot={c.bot_id} size={36} mood={c.status === "RETIRED" ? "asleep" : "still"} />
        <span><b>{CREATURES[c.bot_id]?.name}</b><br /><span className="muted tiny">{c.bot_id}</span></span><StatusBadge status={c.status} /></Link>
      <Grade g={c.demo.grade} label="Demo" />
      <Grade g={c.backtest.grade} label="Backtest" />
      {c.status !== "RETIRED" && (c.demo.grade?.grade === "retire" || c.backtest.grade?.grade === "retire") &&
        <Button size="sm" tone="ghost" onClick={() => retire(c.bot_id)}>Retire</Button>}
    </div>
  );
  return (
    <>
      {msg && <div className="notice">{msg}</div>}
      <div className="report-cards">{cards.filter((c) => c.status !== "RETIRED").map(card)}</div>
      {cards.some((c) => c.status === "RETIRED") && (
        <details className="retired-fold">
          <summary>{cards.filter((c) => c.status === "RETIRED").length} retired</summary>
          <div className="report-cards">{cards.filter((c) => c.status === "RETIRED").map(card)}</div>
        </details>
      )}
    </>
  );
}

/** Who is best and who is ready for real money: one small chart per bot on the same scale, with the live-candidate checks under it. */
function Leaderboard() {
  const { data: rk } = usePoll<Any>("/api/ranking", 15000);
  const { data: hist } = usePoll<Any>("/api/history", 15000);
  const { data: cmp } = usePoll<Any[]>("/api/compare", 30000);
  const series: Record<string, [number, number][]> = {};
  for (const t of [...(hist?.trades ?? [])].reverse()) {
    const s = (series[t.bot_id] ||= []);
    if (!s.length) s.push([new Date(t.entry_time).getTime(), 0]);
    s.push([new Date(t.exit_time).getTime(), (s.at(-1)?.[1] ?? 0) + (t.net ?? 0)]);
  }
  const domain = domainOf(Object.values(series));
  const rows: Any[] = [...(rk?.rows ?? [])].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || b.closed_trades - a.closed_trades);
  const need = Number(String(rk?.rule ?? "").match(/(\d+) closed/)?.[1] ?? 50);
  return (
    <>
      <p className="muted">Rule for a live candidate: {rk?.rule ?? "…"}. Picking the best of {rows.length || 22} bots still favours luck, so treat the top as a shortlist and test it on history it has never seen.
        Every chart below uses the same scale, so a flat line is a bot that has barely traded.</p>
      <div className="multiples">
        {rows.map((r) => {
          const bt = cmp?.find((c) => c.bot_id === r.bot_id)?.latest_backtest;
          const btOk = bt && bt.dataset !== "synthetic" && bt.metrics?.total_return > 0;
          return (
            <Link key={r.bot_id} href={`/bot?id=${r.bot_id}&tab=review`} className={`multiple bc-${r.bot_id.slice(-1)} ${r.eligible ? "eligible" : ""}`}>
              <div className="multiple-head"><span className="who"><Creature bot={r.bot_id} size={24} mood="still" /><b>{CREATURES[r.bot_id]?.name}</b></span>
                {r.rank ? <Pill tone="trade">#{r.rank}</Pill> : null}</div>
              <Sparkline points={series[r.bot_id] ?? []} domain={domain} height={44} label={`${CREATURES[r.bot_id]?.name} cumulative net`} />
              <div className="row-between small">{r.closed_trades ? <Pnl v={r.net} /> : <span className="muted">No trades yet</span>}
                <span className="muted tiny">{r.closed_trades ? `largest fall ${Math.abs(r.max_drawdown_pct).toFixed(1)}%` : ""}</span></div>
              <ProgressBar value={Math.min(r.closed_trades, need)} target={need} label="Trades" />
              <ul className="gate">
                <li className={r.closed_trades > 0 && r.net > 0 ? "ok" : ""}>{r.closed_trades ? "Profitable after costs" : "Profitable after costs (no trades yet)"}</li>
                <li className={btOk ? "ok" : ""}>{bt ? (bt.dataset === "synthetic" ? "Backtest on MT5 history (only synthetic so far)" : "Backtest on MT5 history profitable") : "Backtest on MT5 history (none yet)"}</li>
              </ul>
            </Link>
          );
        })}
      </div>
      <h2>Ranking table</h2>
      <Table>
        <thead><tr><th className="n">Rank</th><th>Bot</th><th className="n">Trades</th><th className="n">Net</th><th className="n">Return</th><th className="n">Largest fall</th>
          <th className="n">Win rate</th><th className="n">Profit factor</th><th>Status</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.bot_id}><td className="n">{r.rank ?? "–"}</td>
            <td><Link href={`/bot?id=${r.bot_id}`} className="who"><Creature bot={r.bot_id} size={24} mood="still" /> {CREATURES[r.bot_id]?.name}</Link></td>
            <td className="n">{r.closed_trades}</td><td className="n">{r.closed_trades ? <Pnl v={r.net} /> : "–"}</td>
            <td className="n">{r.closed_trades && r.return_pct != null ? `${r.return_pct.toFixed(2)}%` : "–"}</td><td className="n">{r.closed_trades ? `${Math.abs(r.max_drawdown_pct).toFixed(2)}%` : "–"}</td>
            <td className="n">{r.win_rate_pct == null ? "–" : `${r.win_rate_pct}%`}</td><td className="n">{r.profit_factor ?? "–"}</td>
            <td className={r.eligible ? "" : "muted"}>{r.eligible ? "▲ candidate" : r.closed_trades ? r.not_eligible_because.join(", ") : "No trades yet"}</td></tr>))}</tbody>
      </Table>
    </>
  );
}

function SuggestionCard({ s, st, onApply, onTest }: { s: Any; st: Any; onApply: (s: Any) => void; onTest: (bot: string) => void }) {
  const stale = s.tested?.stale;
  return (
    <div className={`suggestion k-${s.untested ? "untested" : s.kind}`}>
      <div className="row-between">
        <Link href={`/bot?id=${s.bot_id}&tab=review`} className="who"><Creature bot={s.bot_id} size={30} mood="still" /> {CREATURES[s.bot_id]?.name}</Link>
        {s.tested ? <Pill tone="strong">{s.gain != null ? `confirmed ${money(s.gain, true)}` : "passed"}</Pill>
          : s.untested ? <Pill tone="muted">not tested</Pill> : null}
      </div>
      <b>{s.title}</b>
      {s.change && <p>Change: <code>{changeText(s.change)}</code></p>}
      {s.tested ? <TestNumbers t={s.tested} /> : <p className="muted small">{s.evidence}</p>}
      {s.tested && <p className="tiny muted">Best of several variants tried, so expect less than this. Tested {when(s.reviewed_at)}.</p>}
      {s.untested && <p className="tiny muted">An idea from the trade review. It is not offered until a backtest shows it adds profit without a deeper worst drawdown.</p>}
      {s.rule_change && <p>Needs a rule change: <i>{s.rule_change}</i> (untested; ask for it to be coded).</p>}
      <div className="actions">
        {s.tested && <Button size="sm" onClick={() => onApply(s)} disabled={stale || st?.status === "RUNNING"}
          title={stale ? "The bot's settings changed after this test; test again" : st?.status === "RUNNING" ? "Pause the bot first" : ""}>Apply</Button>}
        {(s.untested || stale) && <Button size="sm" onClick={() => onTest(s.bot_id)}>{stale ? "Test again" : "Test now"}</Button>}
        {(s.id === "no_edge" || s.id === "costs") && st?.status !== "RETIRED" && <Button size="sm" tone="ghost" onClick={() => botAction(s.bot_id, "retire")}>Retire</Button>}
      </div>
    </div>
  );
}

function Suggestions({ data, reload }: { data: Any; reload: () => void }) {
  const { data: bots } = usePoll<Any[]>("/api/bots", 15000);
  const [msg, setMsg] = useState<string | null>(null);
  const status = (b: string) => bots?.find((x) => x.bot_id === b);
  const sugg: Any[] = data?.suggestions ?? [];
  const tests = Object.entries((data?.tests ?? {}) as Record<string, Any>).filter(([, t]) => t);
  const busy = tests.filter(([, t]) => t.status === "QUEUED" || t.status === "RUNNING");
  async function apply(s: Any) {
    try {
      const b = await api.get<Any>(`/api/bots/${s.bot_id}`);
      const m = await applySuggestion(s.bot_id, s, b.config_version ?? 1);
      if (m) { setMsg(m); reload(); }
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  async function test(bot: string) {
    try { setMsg(await testNow(bot)); reload(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  return (
    <>
      <p className="muted">A change is only offered when a backtest shows it adds net profit without a deeper worst drawdown, on the history
        it was found on (first 70%) and again on later history it was not found on (last 30%). Changes are tested after every backtest on MT5 history.</p>
      {msg && <div className="notice">{msg}</div>}
      {busy.length > 0 && <p className="small"><Pill tone="info">Testing</Pill> {busy.map(([b, t]) => `${CREATURES[b]?.name}: ${t.progress}`).join(" · ")}</p>}
      {!sugg.length ? <EmptyState>No suggestions yet. Run backtests on MT5 history; each bot&apos;s changes are tested afterwards.</EmptyState> : (
        <div className="suggestions">
          {sugg.map((s) => s.applied
            ? <AppliedSuggestion key={`${s.bot_id}-${s.id}`} s={s} who={CREATURES[s.bot_id]?.name} />
            : <SuggestionCard key={`${s.bot_id}-${s.id}-${s.review_id}`} s={s} st={status(s.bot_id)} onApply={apply} onTest={test} />)}
        </div>)}
      {tests.length > 0 && <>
        <h2>Every change tried</h2>
        <div className="tests-list">{tests.map(([b, t]) => (
          <div key={b} className="panel" style={{ padding: "var(--s-2) var(--s-3)" }}>
            <Link href={`/bot?id=${b}&tab=review`} className="who"><Creature bot={b} size={22} mood="still" /> {CREATURES[b]?.name}</Link>
            <TestStatus t={t} />
          </div>))}</div>
      </>}
    </>
  );
}

function Blocked({ data }: { data: Any }) {
  const [span, setSpan] = useState("1");
  const { data: funnel } = usePoll<Any[]>(`/api/funnel?days=${span === "1" ? 1 : 7}`, 15000);
  const quiet = (funnel ?? []).every((f) => !f.signals);
  return (
    <>
      <div className="section-head" style={{ marginTop: 0 }}><h2>Why trades did or did not happen</h2>
        <Tabs tabs={[["1", "Last 24 hours"], ["7", "Last 7 days"]]} value={span} onChange={setSpan} /></div>
      {quiet ? <EmptyState>No entry signals in this period: the bots&apos; rules were not met. Each bot&apos;s page shows which check is holding it back.</EmptyState> : (
        <Table>
          <thead><tr><th>Bot</th><th className="n">Signals</th><th className="n">Skipped at sizing</th><th className="n">Held by auditor</th>
            <th className="n">Refused by risk</th><th className="n">Approved</th><th className="n">Filled</th><th>Main reasons</th></tr></thead>
          <tbody>{(funnel ?? []).filter((f) => f.signals).map((f) => (
            <tr key={f.bot_id}>
              <td><Link href={`/bot?id=${f.bot_id}`} className="who"><Creature bot={f.bot_id} size={24} mood="still" /> {CREATURES[f.bot_id]?.name}</Link></td>
              <td className="n">{f.signals}</td><td className="n">{f.skipped_sizing || ""}</td><td className="n">{f.auditor_held || ""}</td>
              <td className="n">{f.risk_rejected || ""}</td><td className="n">{f.approved || ""}</td><td className="n"><b>{f.filled || ""}</b></td>
              <td className="small">{f.top_reasons.map(([k, n]: [string, number]) => `${reasonText(k)} (${n})`).join(", ")}</td>
            </tr>))}</tbody>
        </Table>)}
      <h2>What got blocked (last 7 days)</h2>
      {(data?.blocks ?? []).length === 0 ? <EmptyState>Nothing blocked in the last 7 days.</EmptyState> : (
        <Table>
          <thead><tr><th>Why it was blocked</th><th>Checked by</th><th className="n">Times</th><th>Bots</th><th>Detail</th></tr></thead>
          <tbody>{data.blocks.map((g: Any) => <tr key={g.code}><td title={g.code}>{capitalise(reasonText(g.code))}</td>
            <td>{g.by === "auditor" ? "Market auditor" : "Risk engine"}</td><td className="n">{g.count}</td>
            <td className="small">{g.bots.map((b: string) => CREATURES[b]?.name ?? b).join(", ")}</td><td className="muted small">{blockDetail(g.code, g.example)}</td></tr>)}</tbody>
        </Table>)}
      <h2>Stops and targets put back</h2>
      {(data?.protection ?? []).length === 0 ? <EmptyState>None needed: every open trade has kept its stop-loss and take-profit.</EmptyState> : (
        <ul className="feed">{data.protection.map((e: Any, i: number) => { const p = JSON.parse(e.payload_json || "{}"); return (
          <li key={i}><span className="when">{when(e.ts)}</span><span className="who">{CREATURES[e.bot_id]?.name ?? e.bot_id}</span>
            <span>{e.kind === "PROTECTION_RESTORED" ? "Restored" : "Could not restore"} stop-loss {p.set_sl} and take-profit {p.set_tp} on {p.symbol} trade #{p.ticket}</span><span /></li>); })}</ul>)}
      <h2>Review log</h2>
      {(data?.recent_reviews ?? []).length === 0 ? <EmptyState>No reviews yet.</EmptyState> : (
        <ul className="feed">{data.recent_reviews.map((r: Any) => (
          <li key={r.id}><span className="when">{when(r.created_at)}</span><span className="who"><Creature bot={r.bot_id} size={20} mood="still" /> {CREATURES[r.bot_id]?.name}</span>
            <span>{r.source}</span><Pill>{String(r.trigger).replace(/_/g, " ").toLowerCase()}</Pill></li>))}</ul>)}
    </>
  );
}

const TABS: [string, string][] = [["grades", "Grades"], ["leaderboard", "Leaderboard"], ["suggestions", "Suggestions"], ["blocked", "Blocked trades"]];

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  const tab = TABS.some(([k]) => k === sp.get("tab")) ? sp.get("tab")! : "grades";
  const { data, reload } = usePoll<Any>("/api/assessor", 10000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function runAll() {
    setBusy(true); setMsg(null);
    try { const r = await api.post<Any>("/api/assessor/run"); setMsg(`Reviewed ${r.reviews} times.`); reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  return (
    <>
      <div className="hero">
        <div className="hero-art"><Creature bot="ASSESSOR" size={150} scene full mood={busy ? "busy" : "idle"} /></div>
        <div className="hero-text">
          <h1>The Assessor</h1>
          <p className="muted">Reviews every bot after each closed trade and each finished backtest: what worked, what lost, what got blocked,
            and what to change. It only suggests; you test and apply. It also decides who is ready to be considered for real money.</p>
          <div className="actions"><Button tone="primary" disabled={busy} onClick={runAll}>Review all now</Button>
            <span className="muted small">Last review {when(data?.last_review_at)}</span></div>
        </div>
      </div>
      {msg && <div className="notice">{msg}</div>}
      <Tabs tabs={TABS} value={tab} onChange={(t) => router.replace(`/assessor?tab=${t}`)} />
      {tab === "grades" && <Grades data={data} reload={reload} />}
      {tab === "leaderboard" && <Leaderboard />}
      {tab === "suggestions" && <Suggestions data={data} reload={reload} />}
      {tab === "blocked" && <Blocked data={data} />}
    </>
  );
}

export default function Assessor() {
  return <Suspense fallback={<p className="empty">Loading…</p>}><Inner /></Suspense>;
}
