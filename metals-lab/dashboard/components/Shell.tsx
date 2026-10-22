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

const NAV = [
  ["/", "Overview"], ["/bot", "Bots"], ["/replay", "Backtest & replay"], ["/compare", "Compare"],
  ["/research", "Research"], ["/auditor", "Auditor"], ["/risk", "Risk centre"], ["/settings", "MT5 connection"],
];

function SafetyStrip({ s }: { s: Any }) {
  if (!s) return <div className="strip strip-unknown" role="status">Connecting to the lab backend…</div>;
  const acct = s.account || {};
  const demo = acct.state === "DEMO";
  let tone = "strip-shadow", headline = "Shadow mode: decisions are recorded, no orders are sent";
  if (s.kill) { tone = "strip-danger"; headline = "Kill switch active: entries blocked, positions closing"; }
  else if (!demo) { tone = "strip-danger"; headline = `Execution blocked: account is ${acct.state ?? "unknown"}`; }
  else if (s.armed) { tone = "strip-armed"; headline = "Armed on a verified demo account: bots trade automatically"; }
  return (
    <div className={`strip ${tone}`} role="status">
      <strong>{headline}</strong>
      <span className="strip-meta">
        {s.terminal_kind === "SIMULATED" && <em className="sim-tag">Simulated terminal, not MT5</em>}
        <span>{acct.server ?? "no server"}</span>
        <span>{acct.margin_mode ?? ""}</span>
        {s.global_pause && <span>All bots paused</span>}
        {s.alerts?.length > 0 && <Link href="/risk">{s.alerts.length} open alert{s.alerts.length > 1 ? "s" : ""}</Link>}
      </span>
    </div>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { data: status } = usePoll<Any>("/api/status", 4000);
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
      } catch { /* backend offline; the safety strip shows it */ }
    }
    tick();
    const id = setInterval(tick, 3000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  return (
    <PopupCtx.Provider value={popups}>
      <div className="app">
        <header className="top">
          <Link href="/" className="brand"><Creature bot="S10" size={28} label="" /> <span>Metals creature lab</span></Link>
          <nav aria-label="Sections">
            {NAV.map(([href, label]) => (
              <Link key={href} href={href} className={(href === "/" ? path === "/" : path?.startsWith(href)) ? "on" : ""}>{label}</Link>
            ))}
          </nav>
        </header>
        <SafetyStrip s={status} />
        <main>{children}</main>
        <div className="toasts" aria-live="polite">
          {popups.map((p) => (
            <div key={p.id} className={`toast ${p.net >= 0 ? "gain" : "loss"}`}>
              <Creature bot={p.bot} size={36} />
              <div>
                <b>{CREATURES[p.bot]?.name ?? p.bot} closed a {p.legs > 1 ? `${p.legs}-leg trade` : "trade"}</b>
                <span className="num">{money(p.net, true)} net of costs{p.provisional ? " (provisional)" : ""}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </PopupCtx.Provider>
  );
}
