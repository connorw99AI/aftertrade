"use client";
import Link from "next/link";
import { useState } from "react";
import { StatusBadge } from "@/components/Bits";
import { Creature, CREATURES } from "@/components/Creature";
import { Compare2 } from "@/components/TestResult";
import { Any, api, confirmAction, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { blockDetail, capitalise, reasonText } from "@/lib/wording";

const GRADE_TONE: Record<string, string> = { keep: "gain", retire: "loss", watch: "warnc", "too early": "muted" };

function Grade({ g, label }: { g: Any; label: string }) {
  return <div className="grade"><span className="muted small">{label}</span><b className={GRADE_TONE[g?.grade] ?? "muted"}>{g?.grade ?? "–"}</b><span className="small muted">{g?.why}</span></div>;
}

export default function Assessor() {
  const { data, reload } = usePoll<Any>("/api/assessor", 10000);
  const { data: bots } = usePoll<Any[]>("/api/bots", 15000);
  const [tests, setTests] = useState<Record<string, Any>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const status = (b: string) => bots?.find((x) => x.bot_id === b);

  async function runAll() {
    setBusy(true); setMsg(null);
    try { const r = await api.post<Any>("/api/assessor/run"); setMsg(`Reviewed ${r.reviews} times.`); reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  async function test(s: Any, key: string) {
    setMsg(null);
    try {
      const ds = String(s.source).includes("synthetic") ? "synthetic" : "mt5";
      setTests({ ...tests, [key]: await api.post<Any>(`/api/review/${s.bot_id}/test`, { change: s.change, dataset: ds }) });
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  async function apply(s: Any) {
    const b = await api.get<Any>(`/api/bots/${s.bot_id}`);
    const name = confirmAction(`Apply ${JSON.stringify(s.change)} to ${CREATURES[s.bot_id]?.name}? It must be paused and flat; this is logged.`);
    if (!name) return;
    try {
      const params = Object.fromEntries(Object.entries(b.strategy.params).map(([k, p]: [string, Any]) => [k, p.value]));
      await api.put(`/api/bots/${s.bot_id}/params`, { params: { ...params, ...s.change }, operator: name });
      setMsg(`Applied to ${s.bot_id}.`);
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }
  async function retire(bot: string, why: string) {
    const name = confirmAction(`Retire ${CREATURES[bot]?.name} (${bot})? Its whole balance moves to the other half.`);
    if (!name) return;
    try { const r = await api.post<Any>(`/api/bots/${bot}/retire`, { operator: name, reason: `assessor: ${why}` });
      setMsg(`${bot} retired; ${money(r.moved)} moved to ${r.to ?? "the unallocated pool"}.`); reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }

  const sugg: Any[] = data?.suggestions ?? [];
  const cards: Any[] = [...(data?.cards ?? [])].sort((a, b) => Number(a.status === "RETIRED") - Number(b.status === "RETIRED"));
  const reportCard = (c: Any) => (
    <div key={c.bot_id} className={`report-card ${c.status === "RETIRED" ? "card-retired" : ""}`}>
      <Link href={`/bot?id=${c.bot_id}&tab=review`} className="who"><Creature bot={c.bot_id} size={36} mood={c.status === "RETIRED" ? "asleep" : "idle"} />
        <span><b>{CREATURES[c.bot_id]?.name}</b><br /><span className="muted small">{c.bot_id} · {c.name}</span></span><StatusBadge status={c.status} /></Link>
      <Grade g={c.demo.grade} label="Demo" />
      <Grade g={c.backtest.grade} label="Backtest" />
      {c.status !== "RETIRED" && (c.demo.grade?.grade === "retire" || c.backtest.grade?.grade === "retire") &&
        <button className="btn-retire small-btn" onClick={() => retire(c.bot_id, c.demo.grade?.grade === "retire" ? c.demo.grade.why : c.backtest.grade.why)}>Retire</button>}
    </div>
  );

  return (
    <>
      <div className="hero">
        <div className="hero-art"><Creature bot="ASSESSOR" size={150} scene mood={busy ? "busy" : "idle"} /></div>
        <div className="hero-text">
          <h1>The Assessor</h1>
          <p className="muted">Reviews every bot after each closed trade and each finished backtest: what worked, what lost, what got blocked,
            and what to change. It only suggests; you test and apply. Strength is how far a losing slice sits from zero
            (weak = could easily be chance).</p>
          <div className="actions"><button className="primary" disabled={busy} onClick={runAll}>Review all now</button>
            <span className="muted small">Last review {when(data?.last_review_at)}</span></div>
        </div>
      </div>
      {msg && <div className="warn">{msg}</div>}

      <h2>Suggestions, strongest first</h2>
      {sugg.length === 0 ? <p className="empty">No suggestions yet. They appear once a bot has 10+ closed trades in demo or a backtest.</p> : (
        <div className="suggestions">
          {sugg.map((s, i) => {
            const key = `${s.bot_id}-${s.id}-${s.review_id}`;
            const st = status(s.bot_id);
            return (
              <div key={key} className={`suggestion k-${s.kind}`}>
                <div className="row-between">
                  <Link href={`/bot?id=${s.bot_id}&tab=review`} className="who"><Creature bot={s.bot_id} size={30} /> {CREATURES[s.bot_id]?.name} <span className="muted">{s.bot_id}</span></Link>
                  <span className={`badge strength-${s.strength ?? "none"}`}>{s.strength ?? s.confidence ?? ""}</span>
                </div>
                <b>{s.title}</b>
                <p className="muted small">{s.evidence}</p>
                <p className="small muted">From {s.source}, {when(s.reviewed_at)}</p>
                {s.change && <p>Change: <code>{Object.entries(s.change).map(([k, v]) => `${k} → ${v}`).join(", ")}</code></p>}
                {s.rule_change && <p>Needs a rule change: <i>{s.rule_change}</i></p>}
                <div className="actions">
                  {s.change && <button onClick={() => test(s, key)}>Test on history</button>}
                  {s.change && <button onClick={() => apply(s)} disabled={st?.status === "RUNNING"} title={st?.status === "RUNNING" ? "Pause the bot first" : ""}>Apply</button>}
                  {(s.id === "no_edge" || s.id === "costs") && st?.status !== "RETIRED" && <button className="btn-retire" onClick={() => retire(s.bot_id, s.title)}>Retire this half</button>}
                </div>
                {tests[key] && <Compare2 ids={tests[key]} />}
                <span hidden>{i}</span>
              </div>
            );
          })}
        </div>
      )}

      <h2>Report cards</h2>
      <div className="report-cards">
        {cards.filter((c) => c.status !== "RETIRED").map(reportCard)}
      </div>
      {cards.some((c) => c.status === "RETIRED") && (
        <details className="retired-fold">
          <summary>{cards.filter((c) => c.status === "RETIRED").length} retired halves</summary>
          <div className="report-cards">{cards.filter((c) => c.status === "RETIRED").map(reportCard)}</div>
        </details>
      )}

      <h2>What got blocked (last 7 days)</h2>
      {(data?.blocks ?? []).length === 0 ? <p className="empty">Nothing blocked.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Why it was blocked</th><th>Checked by</th><th className="n">Times</th><th>Bots</th><th>Detail</th></tr></thead>
          <tbody>{data.blocks.map((g: Any) => <tr key={g.code}><td title={g.code}>{capitalise(reasonText(g.code))}</td>
            <td>{g.by === "auditor" ? "Market auditor" : "Risk engine"}</td><td className="n">{g.count}</td>
            <td className="small">{g.bots.join(", ")}</td><td className="muted small">{blockDetail(g.code, g.example)}</td></tr>)}</tbody>
        </table></div>)}

      <h2>Stops and targets put back</h2>
      {(data?.protection ?? []).length === 0 ? <p className="empty">None needed: every open trade has kept its stop-loss and take-profit.</p> : (
        <ul className="feed">{data.protection.map((e: Any, i: number) => { const p = JSON.parse(e.payload_json || "{}"); return (
          <li key={i}><span className="when">{when(e.ts)}</span><span className="who">{e.bot_id}</span>
            <span>{e.kind === "PROTECTION_RESTORED" ? "restored" : "could not restore"} stop-loss {p.set_sl} and take-profit {p.set_tp} on {p.symbol} trade #{p.ticket}</span><span /></li>); })}</ul>)}

      <h2>Review log</h2>
      <ul className="feed">{(data?.recent_reviews ?? []).map((r: Any) => (
        <li key={r.id}><span className="when">{when(r.created_at)}</span><span className="who"><Creature bot={r.bot_id} size={20} /> {r.bot_id}</span>
          <span>{r.source}</span><span className="stage">{r.trigger}</span></li>))}</ul>
    </>
  );
}
