"use client";
import { Creature, CREATURES } from "@/components/Creature";
import { Button, Pill, Pnl } from "@/components/ui";
import { Any, api, confirmAction, money, when } from "@/lib/api";

const NAMES: Record<string, string> = {
  trade_gold: "Gold", trade_silver: "Silver", gold_windows: "Gold windows", silver_windows: "Silver windows",
  breakeven_r: "Breakeven after (R)", trail_atr: "Trailing stop (ATR)",
};

/** A suggestion's settings change in words: "Gold off", "Silver windows → 07:00-24:00 UK". */
export function changeText(change: Record<string, Any> | null | undefined): string {
  return Object.entries(change ?? {}).map(([k, v]) =>
    k.startsWith("trade_") ? `${NAMES[k]} ${v}` : k.endsWith("_windows") ? `${NAMES[k]} → ${v} UK`
      : NAMES[k] ? `${NAMES[k]} → ${Number(v) === 0 ? "off" : v}` : `${k} → ${v}`).join(", ");
}

/** Confirm, then apply the suggestion on the server, which records it as applied. Returns a message, or null if cancelled. */
export async function applySuggestion(bot: string, s: Any, configVersion: number): Promise<string | null> {
  const name = await confirmAction({ title: `Apply this change to ${CREATURES[bot]?.name}?`, confirmLabel: "Apply",
    consequences: [changeText(s.change), `Saved as settings v${configVersion + 1}; the change is logged.`, "The bot must be paused and flat."] });
  if (!name) return null;
  const row = await api.post<Any>(`/api/review/${bot}/apply`,
    { suggestion_id: s.id, change: s.change, operator: name, review_id: s.review_id ?? null, source: s.source ?? null });
  return `Applied to ${CREATURES[bot]?.name} as settings v${row.config_version}.`;
}

/** An applied suggestion, folded to one line; open it to see what it said. */
export function AppliedSuggestion({ s, who }: { s: Any; who?: React.ReactNode }) {
  return (
    <details className="suggestion applied">
      <summary><span className="tick">✓</span> {who}{who ? " · " : ""}Applied in settings v{s.applied.config_version} on {when(s.applied.applied_at)}
        <span className="muted"> · {s.title}</span></summary>
      <p className="muted small">{s.evidence}</p>
      <p>Change: <code>{changeText(s.change)}</code> (by {s.applied.operator})</p>
    </details>
  );
}

const pctDD = (v: number | null | undefined) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
const signed = (v: number | null | undefined) => (v == null ? "–" : money(v, true));

/** The tuned bot against the bot as it stands, on the three periods: search, choose and the untouched final check. */
export function TestNumbers({ t }: { t: Any }) {
  const p = t.periods, o = t.original, n = t.tuned;
  const row = (label: string, k: string, note: string) => o?.[k] && n?.[k] ? (
    <tr key={k}><td>{label}<div className="tiny muted">{p[k][0]} to {p[k][1]} · {note}</div></td>
      <td className="n">{signed(o[k].net)} → <b>{signed(n[k].net)}</b></td>
      <td className="n">{pctDD(o[k].dd)} → <b>{pctDD(n[k].dd)}</b></td>
      <td className="n">{o[k].n} → {n[k].n}</td></tr>) : null;
  return (
    <div className="table-wrap"><table className="test-numbers">
      <thead><tr><th>Period</th><th className="n">Net profit</th><th className="n">Worst drawdown</th><th className="n">Trades</th></tr></thead>
      <tbody>
        {row("Search", "search", "changes tried here")}
        {row("Choose", "choose", "best change kept here")}
        {row("Final check", "final", "never used for tuning")}
      </tbody>
    </table></div>
  );
}

/** Queue an overall assessment of a bot. Returns a message. */
export async function testNow(bot: string): Promise<string> {
  const r = await api.post<Any>(`/api/optimise/${bot}`, {});
  return r.status === "RUNNING" ? `${CREATURES[bot]?.name} is being assessed now.` : `${CREATURES[bot]?.name} is queued for assessment.`;
}

const VERDICT: Record<string, string> = {
  kept: "Kept", passed_not_best: "Passed, but another change did better", unconfirmed: "Helped, but too few trades to confirm",
  noise: "Helped on search, not on choose (probably noise)", no_help: "Did not add profit without a deeper drawdown",
};
const FINAL: Record<string, string> = {
  passed: "passed the final check", overfit: "failed the final check: probably overfit, not offered",
  unconfirmed: "too few trades in the final check to confirm, not offered",
};

/** Key failures the Assessor found in the strategy. */
export function Failures({ items }: { items: Any[] }) {
  if (!items?.length) return null;
  return (
    <ul className="failures">{items.map((f: Any) => (
      <li key={f.code}><b>{f.title}</b>{f.needs_code && <span className="tiny muted"> · needs a code change (sent to the Workshop)</span>}
        <div className="small muted">{f.detail}</div></li>))}</ul>
  );
}

/** Every change the tuner tried in each round, with what happened to it. */
export function ChangesTried({ res }: { res: Any }) {
  return (
    <details>
        <summary className="small">Every change tried ({res.variants_tried})</summary>
        <p className="tiny muted">Rule: {res.rule}. The best of many looks better than it will be.</p>
        {res.rounds.map((r: Any) => (
          <div key={r.round}>
            <div className="small"><b>Round {r.round}</b> {r.kept ? "" : "· nothing kept"}</div>
            <ul className="tried">{r.candidates.map((c: Any) => (
              <li key={c.id} className={`v-${c.verdict}`}><b>{c.title}</b> <span className="muted">— {VERDICT[c.verdict]}.</span>
                <span className="tiny muted"> Search: {signed(r.baseline.search.net)} → {signed(c.search.net)}, drawdown {pctDD(r.baseline.search.dd)} → {pctDD(c.search.dd)}
                  {c.choose && r.baseline.choose ? `; choose: ${signed(r.baseline.choose.net)} → ${signed(c.choose.net)}, drawdown ${pctDD(r.baseline.choose.dd)} → ${pctDD(c.choose.dd)}` : ""}</span></li>))}
            </ul>
          </div>))}
    </details>
  );
}

/** Where a bot's latest assessment stands: progress, key failures, and every change tried in each round. */
export function TestStatus({ t }: { t: Any }) {
  if (!t) return <p className="muted small">Not assessed yet. Press Assess, Test all bots, or run all backtests on MT5 history.</p>;
  if (t.status === "QUEUED" || t.status === "RUNNING")
    return <p className="small"><Pill tone="info">{t.status === "QUEUED" ? "Queued" : "Assessing"}</Pill> {t.progress}</p>;
  if (t.status === "FAILED") return <p className="small notice-danger">Assessment failed: {t.error}</p>;
  const res = t.result;
  const tu = res.tuned;
  return (
    <div className="test-status">
      <p className="small">Assessed {when(t.finished_at)} on {t.dataset === "synthetic" ? "synthetic data" : "MT5 history"} ({t.start} to {t.end}).{" "}
        {tu ? <>Tuned in {tu.steps.length} step(s), {FINAL[tu.verdict]}.</> : <>No change beat the bot as it stands.</>}
        {t.stale && <span className="muted"> Settings changed since; assess again.</span>}</p>
      <Failures items={res.failures} />
      <ChangesTried res={res} />
    </div>
  );
}

/** The Assessor's note on one closed trade: verdict, best/worst point in R, slippage, rule issues. */
export function TradeNote({ n }: { n: Any }) {
  if (!n) return <span className="muted tiny">–</span>;
  const extra = [n.best_r != null ? `best +${n.best_r}R` : null, n.worst_r != null ? `worst -${n.worst_r}R` : null,
    n.slippage_r ? `slippage ${n.slippage_r}R` : null, ...(n.rule_issues ?? [])].filter(Boolean).join(" · ");
  return <div className="trade-note" title={n.summary}><b className={`v-${n.verdict}`}>{n.label}</b>
    <div className="muted">{n.summary}</div>{extra && <div className="muted tiny">{extra}</div>}</div>;
}


/** Send one failure or rule idea to the Engineer (only when you press it). */
export async function sendToEngineer(bot: string, title: string, rule: string, evidence: string): Promise<string | null> {
  const name = await confirmAction({ title: "Send this to the Engineer?", confirmLabel: "Send",
    consequences: [`${CREATURES[bot]?.name}: ${title}`, `Rule to build: ${rule}`, "It becomes a change request in the Workshop tab, ready to copy for Claude Code. Nothing in the bot changes."] });
  if (!name) return null;
  await api.post("/api/workshop/send", { bot_id: bot, title, rule, evidence, operator: name });
  return "Sent to the Engineer: see the Workshop tab.";
}

const pct1 = (v: number | null | undefined) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);

/** One bot's latest assessment in plain words: how it did, what (if anything) to change, what is going wrong. */
export function AssessmentCard({ bot, t, tuned, who, locked, onApply, onAssess, onSent }: {
  bot: string; t: Any; tuned?: Any; who?: boolean; locked?: boolean;
  onApply: (s: Any) => void; onAssess: () => void; onSent: (msg: string) => void;
}) {
  const head = who ? <span className="who"><Creature bot={bot} size={30} mood="still" /> <b>{CREATURES[bot]?.name}</b></span> : null;
  if (!t) return <div className="assessment">{head}<p className="muted small">Not assessed yet.</p><Button size="sm" onClick={onAssess}>Assess</Button></div>;
  if (t.status === "QUEUED" || t.status === "RUNNING")
    return <div className="assessment">{head}<p className="small"><Pill tone="info">{t.status === "QUEUED" ? "Waiting in the queue" : "Assessing now"}</Pill> {t.progress}</p></div>;
  if (t.status === "FAILED")
    return <div className="assessment">{head}<p className="small notice-danger">The assessment failed: {t.error}</p><Button size="sm" onClick={onAssess}>Try again</Button></div>;
  const res = t.result, sm = res.summary ?? {}, tu = res.tuned;
  const verdict = !tu ? { cls: "none", text: "No change beat the current settings. The bot stays as it is." }
    : tu.verdict === "passed" ? { cls: "passed", text: `Better settings found: ${money(tu.gain, true)} more on the last 20% of history, which the tuning never saw.` }
    : tu.verdict === "overfit" ? { cls: "overfit", text: "A change looked better while tuning but lost on the last 20% of history it never saw: probably luck, so it is not offered." }
    : { cls: "overfit", text: "A change looked better but there were too few trades in the last 20% of history to confirm it, so it is not offered." };
  async function send(f: Any) {
    try { const m = await sendToEngineer(bot, f.title, f.rule, f.detail); if (m) onSent(m); } catch (e) { onSent(e instanceof Error ? e.message : String(e)); }
  }
  return (
    <div className="assessment">
      <div className="row-between">{head}<span className="tiny muted">Assessed {when(t.finished_at)} on {t.dataset === "synthetic" ? "synthetic data" : "MT5 history"}, {t.start} to {t.end}
        {t.stale && <b> · settings changed since: assess again</b>}</span></div>
      <div className="assessment-stats">
        <span><b>{sm.n ?? "–"}</b> trades</span>
        <span>net <b><Pnl v={sm.net} /></b></span>
        <span className={sm.expectancy_r > 0 ? "pos" : sm.expectancy_r < 0 ? "neg" : ""} title="Average result per trade as a multiple of what it risked; lot size does not change it. Above 0R = an edge after costs.">
          <b>{sm.expectancy_r != null ? `${sm.expectancy_r > 0 ? "+" : ""}${sm.expectancy_r}R` : "–"}</b> per trade</span>
        <span>win <b>{sm.win_rate != null ? `${sm.win_rate}%` : "–"}</b></span>
        <span>worst drawdown <b>{pct1(sm.max_drawdown)}</b></span>
      </div>
      <div className={`verdict-box v-${verdict.cls}`}>
        <b>{verdict.text}</b>
        {tu && tuned && !tuned.applied && tu.verdict === "passed" && <>
          <p>Change: <code>{changeText(tu.change)}</code> ({tu.steps.map((x: Any) => x.title).join("; ")})</p>
          <TestNumbers t={tuned.tested} />
          <Button size="sm" tone="primary" onClick={() => onApply(tuned)} disabled={t.stale || locked}
            title={t.stale ? "Settings changed after this assessment; assess again" : locked ? "Pause the bot and close its trades first" : ""}>Apply to the bot</Button>
          <span className="tiny muted"> Nothing changes until you press Apply.</span>
        </>}
        {tuned?.applied && <AppliedSuggestion s={tuned} />}
      </div>
      {(res.failures ?? []).length > 0 && <>
        <div className="small"><b>What is going wrong</b></div>
        <ul className="failures">{res.failures.map((f: Any) => (
          <li key={f.code}><b>{f.title}</b><div className="small muted">{f.detail}</div>
            {f.sent ? <span className="tiny muted">Sent to the Engineer</span>
              : <Button size="sm" tone="ghost" onClick={() => send(f)}>Send to Engineer</Button>}</li>))}</ul>
      </>}
      <ChangesTried res={res} />
    </div>
  );
}
