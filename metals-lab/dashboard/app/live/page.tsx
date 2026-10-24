"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { CloseBar, VerdictPill } from "@/components/Bits";
import { CandleChart } from "@/components/CandleChart";
import { BOTS, Creature, CREATURES } from "@/components/Creature";
import { RoleThinking } from "@/components/Thinking";
import { Any } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { Ticks, useTicks } from "@/lib/useStream";

const TFS = ["M1", "M5", "M15", "H1"];
const TF_MIN: Record<string, number> = { M1: 1, M5: 5, M15: 15, H1: 60 };

function LiveChart({ role, title, ticks, overlay, onTf, tf }: {
  role: "GOLD" | "SILVER"; title: string; ticks: Ticks; overlay: Any[]; tf: string; onTf: (t: string) => void;
}) {
  const { data, error, reload } = usePoll<Any>(`/api/chart?role=${role}&tf=${tf}&bars=150`, 15000);
  const t = ticks[role];
  // Merge the live bid into the forming candle; refetch when a new bar starts.
  const merged = useMemo(() => {
    if (!data?.candles?.length || !t) return data;
    const cs = data.candles.slice();
    const last = { ...cs[cs.length - 1] };
    last.c = t.bid;
    last.h = Math.max(last.h, t.bid);
    last.l = Math.min(last.l, t.bid);
    cs[cs.length - 1] = last;
    return { ...data, candles: cs, bid: t.bid, ask: t.ask };
  }, [data, t]);
  const lastStart = data?.candles?.at(-1)?.t;
  const rolled = useRef<string | null>(null);
  useEffect(() => {
    if (!lastStart) return;
    const id = setInterval(() => {
      const end = new Date(lastStart).getTime() + TF_MIN[tf] * 60000;
      if (Date.now() >= end + 1500 && rolled.current !== lastStart) {
        rolled.current = lastStart;
        reload();
      }
    }, 1000);
    return () => clearInterval(id);
  }, [lastStart, tf, reload]);
  const digits = data?.digits ?? 2;
  return (
    <section className={`panel chart-panel chart-${role.toLowerCase()}`}>
      <div className="chart-head">
        <div>
          <h2>{title} <span className="muted">{data?.symbol}</span></h2>
          {merged?.bid != null && <p className="quote num">Bid <b>{Number(merged.bid).toFixed(digits)}</b> · ask {Number(merged.ask).toFixed(digits)} · spread {((merged.ask - merged.bid)).toFixed(digits)}</p>}
        </div>
        <div className="tf-switch" role="group" aria-label={`${title} timeframe`}>
          {TFS.map((x) => <button key={x} className={x === tf ? "on" : ""} aria-pressed={x === tf} onClick={() => onTf(x)}>{x}</button>)}
        </div>
      </div>
      {error ? <div className="warn danger">{error}</div> : <CandleChart data={merged} overlay={overlay} />}
    </section>
  );
}

export default function Live() {
  const { ticks, live } = useTicks();
  const { data: thinking } = usePoll<Any>("/api/thinking", 5000);
  const [tf, setTf] = useState<Record<string, string>>({ GOLD: "M15", SILVER: "M15" });
  const [focus, setFocus] = useState<{ bot: string; role: string } | null>(null);
  const bots = BOTS;
  const overlay = (role: string) => (focus && focus.role === role ? thinking?.[focus.bot]?.roles?.[role]?.levels ?? [] : []);
  function pick(bot: string, role: string) {
    const same = focus?.bot === bot && focus?.role === role;
    setFocus(same ? null : { bot, role });
    const btf = thinking?.[bot]?.timeframe;
    if (!same && btf && TFS.includes(btf)) setTf((x) => ({ ...x, [role]: btf }));
  }
  return (
    <>
      <div className="page-head">
        <h1>Live</h1>
        <p className="muted"><span className={`dot ${live ? "on" : ""}`} /> {live ? "Streaming prices from the terminal" : "Connecting to the price stream…"}.
          Bots decide on completed candles in the backend; this page only shows what they see. Click a cell to put that bot&apos;s levels on the chart.</p>
      </div>
      <div className="live-charts">
        <LiveChart role="GOLD" title="Gold" ticks={ticks} overlay={overlay("GOLD")} tf={tf.GOLD} onTf={(x) => setTf({ ...tf, GOLD: x })} />
        <LiveChart role="SILVER" title="Silver" ticks={ticks} overlay={overlay("SILVER")} tf={tf.SILVER} onTf={(x) => setTf({ ...tf, SILVER: x })} />
      </div>
      <h2>What every bot is thinking</h2>
      <div className="table-wrap">
        <table className="think-grid">
          <thead><tr><th>Bot</th><th>Chart</th><th>Gold</th><th>Silver</th><th>As of bar</th></tr></thead>
          <tbody>
            {bots.map((b) => {
              const th = thinking?.[b];
              return (
                <tr key={b} className={th?.bot_status !== "RUNNING" ? "dim" : ""}>
                  <td><span className="who"><Creature bot={b} size={30} mood={th?.bot_status === "RETIRED" ? "asleep" : th?.bot_status === "RUNNING" ? "idle" : "still"} /> {CREATURES[b].name} <span className="muted">{b}</span></span></td>
                  <td>{th?.timeframe ?? "–"}</td>
                  {(["GOLD", "SILVER"] as const).map((role) => {
                    const r = th?.roles?.[role];
                    const on = focus?.bot === b && focus?.role === role;
                    return (
                      <td key={role}>
                        {r ? (
                          <button className={`cell ${on ? "on" : ""}`} onClick={() => pick(b, role)} title={r.summary}>
                            <VerdictPill verdict={r.verdict} /> <CloseBar r={r} />
                          </button>
                        ) : <span className="muted">–</span>}
                      </td>
                    );
                  })}
                  <td className="muted small">{th?.bar_time ? new Date(th.bar_time).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "–"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {focus && thinking?.[focus.bot] && (
        <div className="panel focus-panel">
          <h3>{CREATURES[focus.bot].name} on {focus.role === "GOLD" ? "gold" : "silver"}</h3>
          <RoleThinking bot={focus.bot} th={thinking[focus.bot]} role={focus.role} />
        </div>
      )}
    </>
  );
}
