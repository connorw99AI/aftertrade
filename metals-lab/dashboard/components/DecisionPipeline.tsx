"use client";
import { useState } from "react";
import { Any, money, utcTime } from "@/lib/api";
import { reasonText } from "@/lib/wording";
import { RoleThinking } from "./Thinking";

/* same rule as the backend's thoughts.py: checks about the clock, not the market */
const TIME_CHECK = /session|trading window|trading hours|liquid hours|^before |cut-off|range complete|friday|evaluation bar/i;
const lower = (s: string) => (s ? s[0].toLowerCase() + s.slice(1) : s);
const hhmm = (m: number) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

type Node = { name: string; state: "ok" | "no" | "warn" | "idle"; text: string };

function codes(json: string | null | undefined): string[] {
  try { return (JSON.parse(json || "[]") as string[]).map((x) => String(x)); } catch { return []; }
}

/** Plain words for a list of risk or auditor reasons; a long list of blocking alerts becomes one phrase. */
export function reasonsText(list: string[]): string {
  return list.map((x) => {
    if (x.startsWith("BLOCKING_ALERTS:")) { const n = x.split(":")[1].split(",").filter(Boolean).length; return `${n} open alert${n === 1 ? "" : "s"} blocking new trades`; }
    return reasonText(x.split(" ")[0].split(":")[0]);
  }).join("; ");
}

/** The bot's latest entry decision in one sentence. */
export function decisionSentence(b: Any): string | null {
  const s = b.last_signal;
  if (!s) return null;
  const sig = s.signal ?? {};
  const metal = sig.role === "SILVER" ? "silver" : "gold";
  const d = sig.role === "SILVER" ? 3 : 2;
  const verb: Record<string, string> = { OPEN_LONG: `open a ${metal} long`, OPEN_SHORT: `open a ${metal} short`, CLOSE_LONG: `close its ${metal} long`, CLOSE_SHORT: `close its ${metal} short` };
  let out = `Wanted to ${verb[sig.intent] ?? String(sig.intent).toLowerCase().replace("_", " ")} at ${utcTime(s.ts)} UTC`;
  if (sig.rationale) out += ` because of a ${sig.rationale}`;
  out += ".";
  if (String(sig.intent).startsWith("OPEN") && sig.stop_price) out += ` Stop ${Number(sig.stop_price).toFixed(d)}${sig.target_price ? `, target ${Number(sig.target_price).toFixed(d)}` : ", no target"}${sig.max_hold_bars ? `, at most ${sig.max_hold_bars} bars` : ""}.`;
  const o = String(s.outcome || "");
  const sameAudit = b.last_audit && b.last_audit.ts === s.ts, sameRisk = b.last_risk && b.last_risk.ts === s.ts;
  if (o.startsWith("EXEC_FILLED") || o.startsWith("EXEC_PARTIAL")) out += " Filled; the auditor and risk approved it.";
  else if (o.startsWith("EXEC_")) out += ` Approved, but the order came back ${o.slice(5).split(":")[0].toLowerCase()}.`;
  else if (o === "SHADOW_APPROVED") out += " Approved by the auditor and risk, but not sent: the lab is in shadow mode.";
  else if (o === "APPROVED") out += " Approved by the auditor and risk.";
  else if (o.startsWith("RISK_")) out += ` Refused by risk: ${sameRisk ? reasonsText(codes(b.last_risk.reasons_json)) : "see the log"}.`;
  else if (o.startsWith("AUDIT_")) out += ` Held by the market auditor: ${sameAudit ? reasonsText(codes(b.last_audit.triggered_json)) || o.slice(6).toLowerCase() : "see the log"}.`;
  else if (o.startsWith("SKIPPED")) out += ` Skipped at sizing: ${reasonsText([o.replace("SKIPPED: ", "")])}.`;
  return out;
}

function nodes(b: Any, th: Any, role: string, nowMin: number): Node[] {
  const r = th?.roles?.[role];
  const side = b.bot_id.endsWith("L") ? "long" : "short";
  const filters: Any[] = (r?.filters ?? []).filter((c: Any) => !String(c.label).toLowerCase().startsWith("metal allowed"));
  const time = filters.filter((c) => TIME_CHECK.test(c.label)), market = filters.filter((c) => !TIME_CHECK.test(c.label));
  const own: Any[] = (r?.[side] ?? []).filter((c: Any) => !String(c.label).toLowerCase().includes("trades allowed"));
  const tw = b.strategy?.trading_window;
  const iv: number[][] = tw?.[role.toLowerCase()]?.intervals ?? tw?.intervals ?? [];
  const opensAt = iv.map(([a]) => a).filter((a) => a > nowMin).sort((x, y) => x - y)[0];
  const tFail = time.find((c) => !c.ok), mFail = market.find((c) => !c.ok), oMiss = own.filter((c) => !c.ok);
  const out: Node[] = [];
  out.push(tFail ? { name: "Session", state: "no", text: opensAt != null ? `Closed: opens ${hhmm(opensAt)} UK` : `Closed: ${lower(tFail.label)}` }
    : { name: "Session", state: "ok", text: time.length ? "Open now" : "Any time" });
  out.push(mFail ? { name: "Filters", state: "no", text: `${lower(mFail.label)}${mFail.detail ? ` (${mFail.detail})` : ""}` }
    : { name: "Filters", state: "ok", text: `All ${market.length} pass` });
  out.push(oMiss.length ? { name: "Setup", state: "no", text: `${own.length - oMiss.length} of ${own.length}: needs ${lower(oMiss[0].label)}` }
    : { name: "Setup", state: "ok", text: `All ${own.length} met` });
  // later stages only count when the latest signal is for this metal and from the bar shown
  const s = b.last_signal;
  const fresh = s && s.signal?.role === role && String(s.signal?.intent).startsWith("OPEN") && th?.as_of && s.ts >= th.as_of;
  const o = String(s?.outcome ?? "");
  const audit = fresh && b.last_audit?.ts === s.ts ? b.last_audit : null, risk = fresh && b.last_risk?.ts === s.ts ? b.last_risk : null;
  let m: Any = {};
  try { m = JSON.parse(risk?.metrics_json || "{}"); } catch { /* none */ }
  let vol: number | null = null;
  try { vol = JSON.parse(audit?.evidence_json || "{}")?.setup?.volume ?? null; } catch { /* none */ }
  if (!fresh) out.push({ name: "Sizing", state: "idle", text: b.risk_budget_per_trade != null ? `Budget ${money(b.risk_budget_per_trade)} a trade` : "not reached" });
  else if (o.startsWith("SKIPPED")) out.push({ name: "Sizing", state: "no", text: reasonsText([o.replace("SKIPPED: ", "")]) });
  else out.push({ name: "Sizing", state: "ok", text: `${vol != null ? `${vol} lots` : "sized"}${m.planned_risk != null ? `, risks ${money(m.planned_risk)}` : ""}` });
  if (!audit) out.push({ name: "Auditor", state: "idle", text: "not reached" });
  else out.push(audit.decision === "APPROVED" ? { name: "Auditor", state: "ok", text: "Approved" }
    : { name: "Auditor", state: "no", text: reasonsText(codes(audit.triggered_json)) || audit.decision.toLowerCase() });
  const used = b.entries_today ?? 0, max = b.max_entries_per_day ?? 0;
  if (risk) out.push(risk.decision === "APPROVED" ? { name: "Risk", state: "ok", text: "Approved" } : { name: "Risk", state: "no", text: reasonsText(codes(risk.reasons_json)) });
  else if (max && used >= max) out.push({ name: "Risk", state: "warn", text: `${used} of ${max} trades used today` });
  else out.push({ name: "Risk", state: "idle", text: max ? `${used} of ${max} trades today` : "not reached" });
  if (!fresh || !risk || risk.decision !== "APPROVED") out.push({ name: "Order", state: "idle", text: "not sent" });
  else if (o === "SHADOW_APPROVED") out.push({ name: "Order", state: "warn", text: "Not sent: shadow mode" });
  else out.push({ name: "Order", state: o.startsWith("EXEC_FILLED") || o.startsWith("EXEC_PARTIAL") || o === "APPROVED" ? "ok" : "no", text: o.startsWith("EXEC_") ? o.slice(5).split(":")[0].toLowerCase() : "sent" });
  return out;
}

/** Session → Filters → Setup → Sizing → Market auditor → Risk → Order, for one metal: where today's entry stopped. */
export function DecisionPipeline({ b, role, nowMin }: { b: Any; role: string; nowMin: number }) {
  const [open, setOpen] = useState(false);
  const th = b.thinking;
  const r = th?.roles?.[role];
  const metal = role === "GOLD" ? "Gold" : "Silver";
  const inTrade = b.open_positions.find((p: Any) => p.role === role);
  if (!r || "waiting" in r) return <section className="panel"><h3 className={`metal-${role}`}>{metal}</h3><p className="empty">Waiting for the first bar to close…</p></section>;
  return (
    <section className="panel">
      <div className="row-between"><h3 className={`metal-${role}`} style={{ margin: 0 }}>{metal}</h3>
        <span className="tiny muted">as of the {th.timeframe} bar at {utcTime(th.as_of)} UTC</span></div>
      {inTrade && <p className="small">In a {inTrade.side.toLowerCase()} since {utcTime(inTrade.opened_at)} UTC, so it is not looking for a new entry here.</p>}
      <ol className="pipeline" style={{ marginTop: 8 }}>
        {nodes(b, th, role, nowMin).map((n) => <li key={n.name} className={n.state}><b>{n.name}</b><span>{n.text}</span></li>)}
      </ol>
      <button className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} the full checklist</button>
      {open && <RoleThinking bot={b.bot_id} th={th} role={role} />}
    </section>
  );
}
