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

/** Numbers behind a tested change: the bot as it is vs with the change, where it was found and where it was confirmed. */
export function TestNumbers({ t }: { t: Any }) {
  const p = t.period, b = t.baseline, c = t.candidate;
  const row = (label: string, range: string, base: Any, cand: Any) => cand && base ? (
    <tr><td>{label}<div className="tiny muted">{range}</div></td>
      <td className="n">{signed(base.net)} → <b>{signed(cand.net)}</b></td>
      <td className="n">{pctDD(base.dd)} → <b>{pctDD(cand.dd)}</b></td>
      <td className="n">{base.n} → {cand.n}</td></tr>) : null;
  return (
    <div className="table-wrap"><table className="test-numbers">
      <thead><tr><th>Period</th><th className="n">Net profit</th><th className="n">Worst drawdown</th><th className="n">Trades</th></tr></thead>
      <tbody>
        {row("Found on", `${p.start} to ${p.found_end}`, b.in, c.in)}
        {row("Confirmed on", `${p.confirm_start} to ${p.end}`, b.out, c.out)}
      </tbody>
    </table></div>
  );
}

/** Queue a test of candidate changes for a bot. Returns a message. */
export async function testNow(bot: string): Promise<string> {
  const r = await api.post<Any>(`/api/optimise/${bot}`, {});
  return r.status === "RUNNING" ? `${CREATURES[bot]?.name} is being tested now.` : `${CREATURES[bot]?.name} is queued for testing.`;
}

const VERDICT: Record<string, string> = {
  passed: "Passed", unconfirmed: "Helped, but too few later trades to confirm", noise: "Helped where found, not later (probably noise)",
  no_help: "Did not add profit without a deeper drawdown",
};

/** Where a bot's latest test stands, and every change it tried with the result. */
export function TestStatus({ t }: { t: Any }) {
  if (!t) return <p className="muted small">Not tested yet. Changes are tested after each backtest on MT5 history, or press Test now.</p>;
  if (t.status === "QUEUED" || t.status === "RUNNING")
    return <p className="small"><Pill tone="info">{t.status === "QUEUED" ? "Queued" : "Testing"}</Pill> {t.progress}</p>;
  if (t.status === "FAILED") return <p className="small notice-danger">Test failed: {t.error}</p>;
  const res = t.result;
  return (
    <details className="test-status">
      <summary className="small">Tested {when(t.finished_at)} on {t.dataset === "synthetic" ? "synthetic data" : "MT5 history"} ({res.period.start} to {res.period.end}): {t.progress}
        {t.stale && <span className="muted"> · settings changed since, test again</span>}</summary>
      <p className="tiny muted">Rule: {res.rule}. {res.variants_tried} variants tried; the best of many looks better than it will be.</p>
      <ul className="tried">{res.candidates.map((c: Any) => (
        <li key={c.id} className={`v-${c.verdict}`}><b>{c.title}</b> <span className="muted">— {VERDICT[c.verdict]}.</span>
          <span className="tiny muted"> Found on: {signed(res.baseline.in.net)} → {signed(c.in.net)}, drawdown {pctDD(res.baseline.in.dd)} → {pctDD(c.in.dd)}
            {c.out && res.baseline.out ? `; later: ${signed(res.baseline.out.net)} → ${signed(c.out.net)}, drawdown ${pctDD(res.baseline.out.dd)} → ${pctDD(c.out.dd)}` : ""}</span></li>))}
      </ul>
    </details>
  );
}
