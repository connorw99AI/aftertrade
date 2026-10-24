"use client";
import { useState } from "react";
import { BOTS, Creature, CREATURES } from "@/components/Creature";
import { Any, apiBase, minutes, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

function exitKind(t: Any): string {
  const r = String(t.exit_rationale || "").toLowerCase();
  if (r.includes("manual close") || r.includes("kill switch")) return "Manual";
  if (r.includes("weekend")) return "Weekend";
  if (r.includes("max holding")) return "Time limit";
  if (r.includes("server-side")) return r.includes("[tp") ? "Take-profit" : r.includes("[sl") ? "Stop-loss" : "Stop/target";
  return "Strategy exit";
}

const dp = (sym: string) => (String(sym).startsWith("XAG") ? 3 : 2);
const px = (v: Any, sym: string) => (v == null ? "–" : Number(v).toFixed(dp(sym)));

function TradeRow({ t, open, onToggle }: { t: Any; open: boolean; onToggle: () => void }) {
  const legs: Any[] = t.legs ?? [];
  const pair = legs.length > 1 || String(t.symbol).includes(",");
  const one = legs[0];
  return (
    <>
      <tr className={`clickable ${open ? "sel" : ""}`} onClick={onToggle}>
        <td>{when(t.entry_time)}</td>
        <td>{when(t.exit_time)}</td>
        <td><span className="who"><Creature bot={t.bot_id} size={24} /> {CREATURES[t.bot_id]?.name ?? t.bot_id}</span></td>
        <td>{pair ? "Gold/silver pair" : <>{t.side === "LONG" ? "▲" : "▼"} {t.volume} {t.symbol}</>}</td>
        <td className="n num">{pair ? legs.map((l) => <div key={l.symbol}>{l.symbol.slice(0, 3)} {px(l.entry_price, l.symbol)}</div>) : px(one?.entry_price ?? t.entry_price, t.symbol)}</td>
        <td className="n num">{pair ? legs.map((l) => <div key={l.symbol}>{l.symbol.slice(0, 3)} {px(l.exit_price, l.symbol)}</div>) : px(one?.exit_price ?? t.exit_price, t.symbol)}</td>
        <td>{minutes(t.minutes_held)}</td>
        <td className={`n num ${t.net >= 0 ? "gain" : "loss"}`}>{money(t.net, true)}{t.provisional ? "*" : ""}</td>
        <td>{exitKind(t)}</td>
      </tr>
      {open && (
        <tr className="detail-row"><td colSpan={9}>
          <div className="trade-detail">
            <div>
              <h3>Legs</h3>
              <table><thead><tr><th>Symbol</th><th>Side</th><th className="n">Lots</th><th className="n">Entry</th><th className="n">Exit</th><th className="n">Stop-loss</th><th className="n">Take-profit</th><th className="n">Move</th></tr></thead>
                <tbody>{legs.map((l) => <tr key={l.symbol + l.opened_at}><td>{l.symbol}</td><td>{l.side.toLowerCase()}</td><td className="n">{l.volume}</td>
                  <td className="n num">{px(l.entry_price, l.symbol)}</td><td className="n num">{px(l.exit_price, l.symbol)}</td>
                  <td className="n num">{l.sl ? px(l.sl, l.symbol) : "none"}</td><td className="n num">{l.tp ? px(l.tp, l.symbol) : "none"}</td>
                  <td className={`n num ${(l.price_move ?? 0) >= 0 ? "gain" : "loss"}`}>{l.price_move != null ? `${l.price_move > 0 ? "+" : ""}${Number(l.price_move).toFixed(dp(l.symbol))}` : "–"}</td></tr>)}</tbody></table>
            </div>
            <dl className="rules">
              <dt>Why it entered</dt><dd>{t.entry_rationale || "–"}</dd>
              <dt>Why it exited</dt><dd>{t.exit_rationale || "–"}</dd>
              <dt>Result</dt><dd>gross {money(t.gross, true)}, commission {money(t.commission, true)}, swap {money(t.swap, true)}, net <b className={t.net >= 0 ? "gain" : "loss"}>{money(t.net, true)}</b>{t.r_multiple != null ? ` (${t.r_multiple}R)` : ""}</dd>
              <dt>Held</dt><dd>{minutes(t.minutes_held)}, from {when(t.entry_time)} to {when(t.exit_time)}</dd>
            </dl>
          </div>
        </td></tr>
      )}
    </>
  );
}

export default function History() {
  const [openId, setOpenId] = useState<string | null>(null);
  const [bot, setBot] = useState("");
  const [symbol, setSymbol] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const qs = new URLSearchParams();
  if (bot) qs.set("bot", bot);
  if (symbol) qs.set("symbol", symbol);
  if (since) qs.set("since", since);
  if (until) qs.set("until", `${until}T23:59:59`);
  const q = qs.toString();
  const { data } = usePoll<Any>(`/api/history${q ? `?${q}` : ""}`, 15000);
  const rows: Any[] = data?.trades ?? [];
  const tot = data?.totals;
  return (
    <>
      <div className="page-head row-between">
        <div><h1>Trade history</h1><p className="muted">Closed demo trades, net of spread, commission and swap. R = result divided by what the trade risked at its stop.</p></div>
        <a className="button-link" href={`${apiBase()}/api/history.csv${q ? `?${q}` : ""}`}>Download CSV</a>
      </div>
      <form className="inline filters" onSubmit={(e) => e.preventDefault()}>
        <label>Bot<select value={bot} onChange={(e) => setBot(e.target.value)}><option value="">All</option>
          {BOTS.map((b) => <option key={b} value={b}>{CREATURES[b].name}</option>)}</select></label>
        <label>Metal<select value={symbol} onChange={(e) => setSymbol(e.target.value)}><option value="">Both</option>
          <option value="XAUUSD">Gold</option><option value="XAGUSD">Silver</option></select></label>
        <label>From<input type="date" value={since} onChange={(e) => setSince(e.target.value)} /></label>
        <label>To<input type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
      </form>
      {tot && (
        <div className="metrics">
          <div className="metric"><span>Trades</span><b>{tot.count}</b></div>
          <div className="metric"><span>Net</span><b className={tot.net >= 0 ? "gain" : "loss"}>{money(tot.net, true)}</b></div>
          <div className="metric"><span>Won / lost</span><b>{tot.wins} / {tot.losses}</b></div>
          <div className="metric"><span>Win rate</span><b>{tot.count ? `${Math.round((tot.wins / tot.count) * 100)}%` : "–"}</b></div>
        </div>
      )}
      {rows.length === 0 ? <p className="empty">No closed trades{q ? " for this filter" : " yet"}.</p> : (
        <div className="table-wrap">
          <table className="history">
            <thead><tr><th>Opened</th><th>Closed</th><th>Bot</th><th>Trade</th><th className="n">Entry</th><th className="n">Exit</th>
              <th>Held</th><th className="n">Profit</th><th>Exit</th></tr></thead>
            <tbody>
              {rows.map((t) => <TradeRow key={t.trade_id} t={t} open={openId === t.trade_id} onToggle={() => setOpenId(openId === t.trade_id ? null : t.trade_id)} />)}
            </tbody>
          </table>
          <p className="muted small">* provisional: costs not yet final in the terminal&apos;s deal history. Click a trade for its stop-loss, take-profit and reasons.</p>
        </div>
      )}
    </>
  );
}
