"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Any, api, money } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { CREATURES, Creature } from "./Creature";

type Popup = { id: number; bot: string; net: number; provisional: boolean; legs: number };
const PopupCtx = createContext<Popup[]>([]);
export const usePopups = () => useContext(PopupCtx);
const SummaryCtx = createContext<Any>(null);
/** The /api/summary poll shared by every page (account strip, bots, alerts). */
export const useSummary = () => useContext(SummaryCtx);

const NAV: [string, string][] = [
  ["/", "Overview"], ["/live", "Live"], ["/positions", "Positions"], ["/assessor", "Assessor"], ["/activity", "Activity"], ["/history", "History"],
  ["/compare", "Compare"], ["/backtest", "Backtest"], ["/system", "System"],
];

function SafetyChip({ s, sum }: { s: Any; sum: Any }) {
  if (!s) return <span className="chip chip-unknown">Connecting…</span>;
  const acct = s.account || {};
  if (s.kill) return <Link href="/system" className="chip chip-danger">Kill switch active</Link>;
  if (acct.state !== "DEMO") return <Link href="/system" className="chip chip-danger">Blocked: account {acct.state ?? "unknown"}</Link>;
  return (
    <span className={`chip ${s.armed ? "chip-armed" : "chip-shadow"}`} title={`${acct.server ?? ""} ${acct.margin_mode ?? ""}`}>
      {s.armed ? "Armed, demo" : "Shadow mode"}{s.terminal_kind === "SIMULATED" ? " (simulated)" : ""}{sum?.global_pause ? ", all paused" : ""}
    </span>
  );
}

function AccountStrip({ sum, status }: { sum: Any; status: Any }) {
  const a = sum?.account || {};
  const cur = a.currency ? ` ${a.currency}` : "";
  const c = sum?.counts || {};
  const alerts: Any[] = sum?.alerts ?? [];
  return (
    <div className="acct" role="status">
      <div className="acct-cell"><span>Equity</span><b className="num">{money(a.equity)}{cur}</b></div>
      <div className="acct-cell"><span>Balance</span><b className="num">{money(a.balance)}</b></div>
      <div className="acct-cell"><span>Today</span><b className={`num ${(sum?.today_pnl ?? 0) >= 0 ? "gain" : "loss"}`}>{money(sum?.today_pnl, true)}</b></div>
      <div className="acct-cell"><span>Open trades</span><b className="num">{sum?.open_trades ?? "–"}</b></div>
      <div className="acct-cell"><span>Bots</span><b className="num">{c.RUNNING ?? 0} running · {(c.PAUSED ?? 0) + (c.PAUSED_RISK ?? 0)} paused · {c.STOPPED ?? 0} stopped · {c.RETIRED ?? 0} retired</b></div>
      {sum?.stopped_for_day > 0 && <div className="acct-cell"><span>Stopped for today</span><b className="num">{sum.stopped_for_day}</b></div>}
      <div className="acct-cell grow">
        {alerts.length > 0 && <Link href="/activity" className="alert-pill">{alerts.length} alert{alerts.length > 1 ? "s" : ""}</Link>}
        <SafetyChip s={status} sum={sum} />
      </div>
    </div>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { data: status } = usePoll<Any>("/api/status", 5000);
  const { data: summary } = usePoll<Any>("/api/summary", 3000);
  const [popups, setPopups] = useState<Popup[]>([]);
  const last = useRef<number | null>(null);

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

  return (
    <SummaryCtx.Provider value={summary}>
      <PopupCtx.Provider value={popups}>
        <div className="app">
          <header className="top">
            <Link href="/" className="brand"><Creature bot="ASSESSOR" size={30} label="" /> <span>Metals creature lab</span></Link>
            <nav aria-label="Sections">
              {NAV.map(([href, label]) => (
                <Link key={href} href={href} className={(href === "/" ? path === "/" || path?.startsWith("/bot") : path?.startsWith(href)) ? "on" : ""}>{label}</Link>
              ))}
            </nav>
          </header>
          <AccountStrip sum={summary} status={status} />
          <main>{children}</main>
          <div className="toasts" aria-live="polite">
            {popups.map((p) => (
              <div key={p.id} className={`toast ${p.net >= 0 ? "gain" : "loss"}`}>
                <Creature bot={p.bot} size={40} mood={p.net >= 0 ? "happy" : "hurt"} />
                <div>
                  <b>{CREATURES[p.bot]?.name ?? p.bot} closed a {p.legs > 1 ? `${p.legs}-leg trade` : "trade"}</b>
                  <span className="num">{money(p.net, true)} net of costs{p.provisional ? " (provisional)" : ""}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </PopupCtx.Provider>
    </SummaryCtx.Provider>
  );
}
