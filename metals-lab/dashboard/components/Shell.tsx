"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Any, api, confirmAction, money, setCurrency, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { alertGroupTitle, alertKind } from "@/lib/wording";
import { BOTS, CREATURES, Creature, StateSymbol } from "./Creature";
import { Button, DialogHost, Pnl } from "./ui";

type Popup = { id: number; bot: string; net: number; provisional: boolean; legs: number };
const PopupCtx = createContext<Popup[]>([]);
export const usePopups = () => useContext(PopupCtx);
const SummaryCtx = createContext<Any>(null);
/** The /api/summary poll shared by every page (account strip, bots, alerts). */
export const useSummary = () => useContext(SummaryCtx);

/** Six places, each named for a job. Old addresses redirect here. */
const NAV: [string, string, string[]][] = [
  ["/", "Lab", ["/bot"]], ["/live", "Live", []], ["/trades", "Trades", ["/positions", "/history"]],
  ["/assessor", "Assessor", ["/compare"]], ["/research", "Research", ["/backtest"]], ["/system", "System", ["/activity", "/risk", "/auditor", "/settings"]],
];

function SafetyChip({ s, sum }: { s: Any; sum: Any }) {
  if (!s) return <span className="chip">Connecting…</span>;
  const acct = s.account || {};
  if (s.kill) return <Link href="/system" className="chip chip-danger">Kill switch on</Link>;
  if (acct.state !== "DEMO") return <Link href="/system?tab=mt5" className="chip chip-danger">Blocked: account {acct.state ?? "unknown"}</Link>;
  return (
    <span className={`chip ${s.armed ? "chip-armed" : "chip-shadow"}`} title={`${acct.server ?? ""} ${acct.margin_mode ?? ""}`}>
      {s.armed ? "Armed, demo" : "Shadow mode"}{s.terminal_kind === "SIMULATED" ? " (simulated)" : ""}{sum?.global_pause ? ", all paused" : ""}
    </span>
  );
}

function Counters({ sum }: { sum: Any }) {
  const c = sum?.counts || {};
  const items: [Parameters<typeof StateSymbol>[0]["state"], number, string][] = [
    [sum?.kill ? "kill" : "run", c.RUNNING ?? 0, "running"], ["pause", (c.PAUSED ?? 0) + (c.PAUSED_RISK ?? 0), "paused"],
    ["stop", c.STOPPED ?? 0, "stopped"], ["retired", c.RETIRED ?? 0, "retired"],
  ];
  return (
    <div className="counters" aria-label="Bots by state">
      {items.map(([st, n, word]) => <Link key={word} href="/" className="counter" title={`${n} ${word}`}><StateSymbol state={st} size={22} />{n}<span className="sr-only"> {word}</span></Link>)}
    </div>
  );
}

/** Alerts of the same kind (several vanished trades, say) shown once with a count and resolved together. */
export function groupAlerts(alerts: Any[]): Any[] {
  return Object.values(alerts.reduce((acc: Record<string, Any>, a) => {
    const k = `${a.severity}|${alertKind(a.code)}`;
    (acc[k] ??= { ...a, kind: alertKind(a.code), codes: [] as string[], messages: [] as string[], first: a.ts, last: a.ts }).codes.push(a.code);
    acc[k].messages.push(a.message);
    if (a.ts < acc[k].first) acc[k].first = a.ts;
    if (a.ts > acc[k].last) acc[k].last = a.ts;
    return acc;
  }, {})) as Any[];
}

function AlertsDrawer({ alerts, onClose }: { alerts: Any[]; onClose: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const groups = groupAlerts(alerts);
  async function resolve(g: Any) {
    const n = g.codes.length;
    const name = await confirmAction({ title: n > 1 ? `Resolve all ${n} of these alerts?` : "Resolve this alert?", confirmLabel: "Resolve",
      consequences: [n > 1 ? alertGroupTitle(g.kind, n) : g.message, g.blocks_entries ? "New entries are no longer blocked by it." : "It does not block entries."] });
    if (!name) return;
    setErr(null);
    try { for (const code of g.codes) await api.post(`/api/alerts/${code}/resolve`, { operator: name, reason: "resolved from dashboard" }); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Open alerts" onKeyDown={(e) => e.key === "Escape" && onClose()}>
        <div className="drawer-head"><h2>Open alerts</h2><Button tone="ghost" onClick={onClose} aria-label="Close">✕</Button></div>
        {err && <div className="notice notice-danger">{err}</div>}
        {groups.length === 0 ? <p className="empty">No open alerts.</p> : (
          <ul className="alert-list">
            {groups.map((g) => (
              <li key={g.id} className={`sev-${g.severity}`}>
                <span className="tiny muted">{g.severity.toLowerCase()} · {g.codes.length > 1 ? `${when(g.first)} to ${when(g.last)}` : when(g.ts)}{g.blocks_entries ? " · blocks new entries" : ""}</span>
                {g.codes.length > 1 ? (
                  <details><summary style={{ color: "var(--text)" }}>{alertGroupTitle(g.kind, g.codes.length)}</summary>
                    <ul className="alert-items">{g.messages.map((m: string, i: number) => <li key={i}>{m}</li>)}</ul></details>
                ) : <span>{g.message}</span>}
                <div><Button size="sm" onClick={() => resolve(g)}>{g.codes.length > 1 ? `Resolve all ${g.codes.length}` : "Resolve"}</Button></div>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </>
  );
}

/** Press / anywhere to jump to a bot by name. */
function JumpPalette({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const router = useRouter();
  const hits = BOTS.filter((b) => `${CREATURES[b].name} ${b}`.toLowerCase().includes(q.toLowerCase())).slice(0, 12);
  const go = (b: string) => { onClose(); router.push(`/bot?id=${b}`); };
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog palette" role="dialog" aria-label="Jump to a bot">
        <input autoFocus placeholder="Jump to a bot…" value={q} onChange={(e) => { setQ(e.target.value); setI(0); }}
               onKeyDown={(e) => {
                 if (e.key === "Escape") onClose();
                 if (e.key === "ArrowDown") { e.preventDefault(); setI(Math.min(hits.length - 1, i + 1)); }
                 if (e.key === "ArrowUp") { e.preventDefault(); setI(Math.max(0, i - 1)); }
                 if (e.key === "Enter" && hits[i]) go(hits[i]);
               }} />
        <ul>{hits.map((b, k) => <li key={b}><a href={`/bot?id=${b}`} className={k === i ? "on" : ""} onClick={(e) => { e.preventDefault(); go(b); }}>
          <Creature bot={b} size={28} mood="still" /> {CREATURES[b].name} <span className="muted tiny">{b}</span></a></li>)}</ul>
        <p className="tiny muted">On a bot card or bot page: <kbd>S</kbd> start, <kbd>P</kbd> pause. <kbd>/</kbd> opens this.</p>
      </div>
    </div>
  );
}

function AccountStrip({ sum, status }: { sum: Any; status: Any }) {
  const a = sum?.account || {};
  return (
    <div className="acct" role="status">
      <div className="acct-cell"><span>Equity</span><b>{money(a.equity)}</b></div>
      <div className="acct-cell"><span>Balance</span><b>{money(a.balance)}</b></div>
      <div className="acct-cell"><span>Today</span><b><Pnl v={sum?.today_pnl} /></b></div>
      <div className="acct-cell"><span>Open trades</span><b>{sum?.open_trades ?? "–"}</b></div>
      {sum?.stopped_for_day > 0 && <div className="acct-cell"><span>Done for today</span><b>{sum.stopped_for_day}</b></div>}
      <div className="acct-cell grow"><span className="chip chip-env" title="Every bot trades the demo account">DEMO</span><SafetyChip s={status} sum={sum} /></div>
    </div>
  );
}

const BELL = <svg viewBox="0 0 24 24" aria-hidden><path d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-5.5-6.84V3a1.5 1.5 0 0 0-3 0v1.16A7 7 0 0 0 5 11v5l-2 2v1h18v-1Z" /></svg>;

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { data: status } = usePoll<Any>("/api/status", 5000);
  const { data: summary, error: summaryError } = usePoll<Any>("/api/summary", 3000);
  const [popups, setPopups] = useState<Popup[]>([]);
  const [drawer, setDrawer] = useState(false);
  const [jump, setJump] = useState(false);
  const last = useRef<number | null>(null);
  setCurrency(summary?.account?.currency);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); setJump(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const evs = await api.get<Any[]>(`/api/events?after=${last.current ?? 0}`);
        if (last.current === null) {
          // First load: remember where we are, never replay old closes as fresh popups.
          last.current = evs.length ? evs[evs.length - 1].id : 0;
          if (evs.length === 100) last.current = (await api.get<Any[]>(`/api/events?after=${last.current}`)).at(-1)?.id ?? last.current;
          return;
        }
        for (const e of evs) {
          last.current = e.id;
          if (e.kind === "TRADE_CLOSED") {
            const p: Popup = { id: e.id, bot: e.bot_id, net: e.payload.net, provisional: !!e.payload.provisional, legs: e.payload.legs };
            setPopups((ps) => [...ps.slice(-4), p]);
            setTimeout(() => !stop && setPopups((ps) => ps.filter((x) => x.id !== p.id)), 6500);
          }
        }
      } catch { /* backend offline; the safety chip shows it */ }
    }
    tick();
    const id = setInterval(tick, 3000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  const alerts: Any[] = summary?.alerts ?? [];
  const on = (href: string, also: string[]) => (href === "/" ? path === "/" : path?.startsWith(href)) || also.some((a) => path?.startsWith(a));
  return (
    <SummaryCtx.Provider value={summary}>
      <PopupCtx.Provider value={popups}>
        <div className="app">
          <header className="top">
            <Link href="/" className="brand"><img src="/brand/icon-64.png" width={34} height={34} alt="" /> <span>Metal Lab</span></Link>
            <div className="nav-wrap">
              <nav className="nav" aria-label="Sections">
                {NAV.map(([href, label, also]) => <Link key={href} href={href} className={on(href, also) ? "on" : ""}>{label}</Link>)}
              </nav>
            </div>
            <div className="hdr-tools">
              <Counters sum={summary} />
              <button className={`bell ${alerts.length ? "" : "quiet"}`} onClick={() => setDrawer(true)} aria-label={`${alerts.length} open alerts`} title="Open alerts">
                {BELL}{alerts.length > 0 && <span className="count">{alerts.length}</span>}
              </button>
            </div>
          </header>
          <AccountStrip sum={summary} status={status} />
          {/* pages wait for the first summary, so every amount already knows the account currency */}
          <main>{summary || summaryError ? children : <p className="empty">Connecting to the lab…</p>}</main>
          {drawer && <AlertsDrawer alerts={alerts} onClose={() => setDrawer(false)} />}
          {jump && <JumpPalette onClose={() => setJump(false)} />}
          <DialogHost />
          <div className="toasts" aria-live="polite">
            {popups.map((p) => (
              <div key={p.id} className={`toast ${p.net >= 0 ? "is-up" : "is-down"}`}>
                <Creature bot={p.bot} size={40} mood={p.net >= 0 ? "happy" : "hurt"} />
                <div>
                  <b>{CREATURES[p.bot]?.name ?? p.bot} closed a {p.legs > 1 ? `${p.legs}-leg trade` : "trade"}</b>
                  <span><Pnl v={p.net} /> net of costs{p.provisional ? " (provisional)" : ""}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </PopupCtx.Provider>
    </SummaryCtx.Provider>
  );
}
