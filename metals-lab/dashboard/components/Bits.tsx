"use client";
import { useState } from "react";
import { Any, api, confirmAction } from "@/lib/api";

export const STATUS_TEXT: Record<string, string> = {
  RUNNING: "Running", PAUSED: "Paused", PAUSED_RISK: "Paused (drawdown)", STOPPED: "Stopped",
};

export function StatusBadge({ status, extra }: { status: string; extra?: string | null }) {
  return <span className={`badge st-${status}`}>{STATUS_TEXT[status] ?? status}{extra ? ` · ${extra}` : ""}</span>;
}

export const VERDICT: Record<string, { text: string; tone: string }> = {
  ENTRY_LONG: { text: "Entry long", tone: "v-entry" },
  ENTRY_SHORT: { text: "Entry short", tone: "v-entry" },
  NO_ENTRY: { text: "No entry", tone: "v-none" },
  IN_TRADE_LONG: { text: "In long", tone: "v-trade" },
  IN_TRADE_SHORT: { text: "In short", tone: "v-trade" },
  WAITING_DATA: { text: "Waiting for data", tone: "v-wait" },
};

export function VerdictPill({ verdict }: { verdict?: string }) {
  const v = VERDICT[verdict ?? ""] ?? { text: verdict ? verdict.toLowerCase() : "–", tone: "v-wait" };
  return <span className={`verdict ${v.tone}`}>{v.text}</span>;
}

/** How close a bot is to an entry on one metal: share of filters plus the better side's conditions that are met. */
export function closeness(r: Any): { score: number; side: "long" | "short" | null; met: number; total: number } {
  if (!r || "waiting" in r || !r.filters) return { score: 0, side: null, met: 0, total: 0 };
  const f = r.filters as Any[];
  const fm = f.filter((c) => c.ok).length;
  let best = { score: 0, side: null as "long" | "short" | null, met: 0, total: 0 };
  for (const side of ["long", "short"] as const) {
    const s = (r[side] ?? []) as Any[];
    const total = f.length + s.length;
    const met = fm + s.filter((c) => c.ok).length;
    const score = total ? met / total : 0;
    if (score > best.score) best = { score, side, met, total };
  }
  return best;
}

export function CloseBar({ r }: { r: Any }) {
  const c = closeness(r);
  if (!c.total) return null;
  return (
    <span className="closebar" title={`${c.met} of ${c.total} conditions met${c.side ? ` (${c.side})` : ""}`}>
      <span style={{ width: `${Math.round(c.score * 100)}%` }} className={c.score >= 0.99 ? "full" : c.score >= 0.75 ? "near" : ""} />
    </span>
  );
}

/** Start / Pause / Stop, only the ones that change the current state, coloured by meaning. */
export function BotControls({ bot, status, hasOpen, onDone }: { bot: string; status: string; hasOpen: boolean; onDone?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function act(action: "start" | "pause" | "stop", e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    const name = confirmAction(`${action === "start" ? "Start" : action === "pause" ? "Pause new entries for" : "Stop"} ${bot}?`);
    if (!name) return;
    setBusy(true);
    setErr(null);
    try {
      await api.post(`/api/bots/${bot}/${action}`, { operator: name, reason: `${action} from dashboard` });
      onDone?.();
    } catch (x) {
      setErr(x instanceof Error ? x.message : String(x));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="controls" onClick={(e) => e.stopPropagation()}>
      {status !== "RUNNING" && <button className="btn-go" disabled={busy} onClick={(e) => act("start", e)}>Start</button>}
      {status === "RUNNING" && <button className="btn-pause" disabled={busy} onClick={(e) => act("pause", e)}>Pause</button>}
      {status !== "STOPPED" && (
        <button className="btn-stop" disabled={busy || hasOpen} title={hasOpen ? "Close its trades first, or pause it" : ""}
                onClick={(e) => act("stop", e)}>Stop</button>
      )}
      {err && <span className="err">{err}</span>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}
