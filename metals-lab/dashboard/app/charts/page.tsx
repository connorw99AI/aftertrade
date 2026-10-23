"use client";
import { useState } from "react";
import { CandleChart, useNarrow } from "@/components/CandleChart";
import { CREATURES } from "@/components/Creature";
import { RoleThinking } from "@/components/Thinking";
import { Any } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const TFS = ["M1", "M5", "M15", "H1", "H4", "D1"];

function MetalPanel({ role, title, thinking }: { role: "GOLD" | "SILVER"; title: string; thinking: Any }) {
  const [tf, setTf] = useState("M15");
  const [watch, setWatch] = useState<string>("");
  const bars = useNarrow() ? 70 : 150;
  const { data, error } = usePoll<Any>(`/api/chart?role=${role}&tf=${tf}&bars=${bars}`, tf === "M1" ? 3000 : 6000);
  const digits = data?.digits ?? 2;
  const first = data?.candles?.[0]?.o;
  const lastC = data?.candles?.at(-1)?.c;
  const change = first && lastC ? ((lastC - first) / first) * 100 : null;
  return (
    <section className={`panel chart-panel chart-${role.toLowerCase()}`} aria-label={`${title} chart`}>
      <div className="chart-head">
        <div>
          <h2>{title} <span className="muted">{data?.symbol}</span></h2>
          {data?.bid != null && (
            <p className="quote num">
              Bid {data.bid.toFixed(digits)}, ask {data.ask.toFixed(digits)}
              {change !== null && <span className={change >= 0 ? "gain" : "loss"}> {change >= 0 ? "+" : ""}{change.toFixed(2)}% over view</span>}
            </p>
          )}
        </div>
        <div className="tf-switch" role="group" aria-label={`${title} timeframe`}>
          {TFS.map((t) => (
            <button key={t} className={t === tf ? "on" : ""} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>
          ))}
        </div>
      </div>
      <label className="overlay-pick">Show a bot&apos;s levels on this chart
        <select value={watch} onChange={(e) => { setWatch(e.target.value); const tfw = thinking?.[e.target.value]?.timeframe; if (tfw) setTf(tfw); }}>
          <option value="">None</option>
          {Object.keys(CREATURES).filter((b) => thinking?.[b]?.roles?.[role]).map((b) => (
            <option key={b} value={b}>{CREATURES[b].name} ({b}, {thinking[b].timeframe})</option>
          ))}
        </select>
      </label>
      {error ? <div className="warn danger">{error}</div>
        : <CandleChart data={data} overlay={watch ? (thinking?.[watch]?.roles?.[role]?.levels ?? []) : []} />}
      {watch && thinking?.[watch] && <RoleThinking bot={watch} th={thinking[watch]} role={role} />}
      {data?.positions?.length > 0 && (
        <ul className="chart-pos">
          {data.positions.map((p: Any, i: number) => (
            <li key={i}>{CREATURES[p.bot_id]?.name} ({p.bot_id}): {p.side.toLowerCase()} {p.volume} lots from {Number(p.entry).toFixed(digits)}
              {p.sl ? `, stop ${Number(p.sl).toFixed(digits)}` : ""}{p.tp ? `, target ${Number(p.tp).toFixed(digits)}` : ""}</li>
          ))}
        </ul>
      )}
      <h3 className="see-title">What each bot sees on {title.toLowerCase()}</h3>
      {!thinking ? <p className="empty">Waiting for the first bar to close…</p> : (
        <div className="thinking-list">
          {Object.keys(CREATURES).map((b) => thinking[b]?.roles?.[role]
            ? <RoleThinking key={b} bot={b} th={thinking[b]} role={role} compact /> : null)}
        </div>
      )}
    </section>
  );
}

export default function Charts() {
  const { data: thinking } = usePoll<Any>("/api/thinking", 5000);
  return (
    <>
      <h1>Charts</h1>
      <p className="muted">Live prices from your terminal. Lines show each bot&apos;s open position: entry dashed, stop in red, target in green.
        The faded candle on the right is still forming.
        Below each chart, every bot shows its entry checklist: tap one to see which conditions are met.</p>
      <div className="charts">
        <MetalPanel role="GOLD" title="Gold" thinking={thinking} />
        <MetalPanel role="SILVER" title="Silver" thinking={thinking} />
      </div>
    </>
  );
}
