"use client";
import { CREATURES } from "@/components/Creature";
import { Pill } from "@/components/ui";
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

