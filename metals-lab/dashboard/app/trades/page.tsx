"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useState } from "react";
import { Tabs } from "@/components/Bits";
import { BOTS, Creature, CREATURES } from "@/components/Creature";
import { useSummary } from "@/components/Shell";
import { Button, DateInput, EmptyState, Pnl, Stat, Stats, Table } from "@/components/ui";
import { Any, api, apiBase, confirmAction, minutes, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { RangeBar } from "@/components/RangeBar";

const dp = (sym: string) => (String(sym).startsWith("XAG") ? 3 : 2);
const px = (v: Any, sym: string) => (v == null ? "–" : Number(v).toFixed(dp(sym)));

function OpenTrades() {
  const { data, error, reload } = usePoll<Any>("/api/positions", 2000);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rows: Any[] = data?.positions ?? [];
  const total = rows.reduce((s, p) => s + (p.profit ?? 0), 0);
  const bots = Array.from(new Set(rows.map((p) => p.bot_id)));

  async function close(scope: "position" | "bot" | "all", target: string | null, what: string, n: number) {
    const name = await confirmAction({ title: `Close ${what}?`, tone: "danger", confirmLabel: n > 1 ? `Close ${n} trades` : "Close trade",
      consequences: [`${n} trade${n > 1 ? "s close" : " closes"} at market now.`, "Each close goes through the auditor and risk checks like any exit."] });
    if (!name) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api.post<Any>("/api/positions/close", { scope, target, operator: name });
      setMsg(r.results.map((x: Any) => `${x.position}: ${x.outcome}`).join(" · "));
      reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return (
    <>
      <div className="row-between">
        <p className="muted">Every trade the bots hold, live from the terminal. &quot;Protected&quot; means the stop-loss and take-profit in MT5 match what the bot opened with; the auditor checks and restores them every cycle.</p>
        {rows.length > 0 && <Button tone="danger" disabled={busy} onClick={() => close("all", null, `all ${rows.length} open trades`, rows.length)}>Close everything</Button>}
      </div>
      {msg && <div className="notice">{msg}</div>}
      {error && <div className="notice notice-danger">{error}</div>}
      {rows.length === 0 ? <EmptyState>No open trades. Every bot is flat.</EmptyState> : <>
        <Stats>
          <Stat label="Open trades" value={rows.length} />
          <Stat label="Open P&L" value={<Pnl v={total} />} />
          <Stat label="Bots in a trade" value={bots.length} />
        </Stats>
        <Table>
          <thead><tr><th>Bot</th><th>Trade</th><th className="n">Entry</th><th className="n">Now</th><th className="n">P&amp;L</th>
            <th>Stop ← → target</th><th>Open for</th><th>Protected</th><th /></tr></thead>
          <tbody>{rows.map((p) => (
            <tr key={p.position_key}>
              <td><Link href={`/bot?id=${p.bot_id}`} className="who"><Creature bot={p.bot_id} size={28} mood="busy" /> {CREATURES[p.bot_id]?.name}</Link></td>
              <td>{p.side === "LONG" ? "▲ Long" : "▼ Short"} {p.volume_open} {p.symbol}{p.group_id && p.group_id.startsWith("S10") ? <span className="muted"> (pair)</span> : null}</td>
              <td className="n">{px(p.entry_price, p.symbol)}</td><td className="n">{px(p.price, p.symbol)}</td>
              <td className="n"><Pnl v={p.profit} /></td>
              <td><RangeBar p={p} /></td>
              <td>{minutes(p.minutes_open)}</td>
              <td>{p.protection_ok ? <span>✓ yes</span> : <span className="err" title={`In MT5: SL ${p.terminal_sl ?? "none"}, TP ${p.terminal_tp ?? "none"}`}>✗ being restored</span>}</td>
              <td><Button size="sm" tone="danger" disabled={busy} onClick={() => close("position", p.position_key, `${CREATURES[p.bot_id]?.name}'s ${p.side.toLowerCase()} ${p.symbol}`, 1)}>Close</Button></td>
            </tr>))}</tbody>
        </Table>
        {bots.length > 1 && (
          <div className="actions" style={{ marginTop: 12 }}>
            <span className="muted">Close all of one bot&apos;s trades:</span>
            {bots.map((b) => <Button key={b} size="sm" disabled={busy} onClick={() => close("bot", b, `all of ${CREATURES[b]?.name}'s trades`, rows.filter((r) => r.bot_id === b).length)}>{CREATURES[b]?.name}</Button>)}
          </div>
        )}
      </>}
    </>
  );
}

function exitKind(t: Any): string {
  const r = String(t.exit_rationale || "").toLowerCase();
  if (r.includes("manual close") || r.includes("kill switch")) return "Manual";
  if (r.includes("weekend")) return "Weekend";
  if (r.includes("max holding")) return "Time limit";
  if (r.includes("server-side")) return r.includes("[tp") ? "Take-profit" : r.includes("[sl") ? "Stop-loss" : "Stop/target";
  return "Strategy exit";
}

function TradeRow({ t, open, onToggle }: { t: Any; open: boolean; onToggle: () => void }) {
  const legs: Any[] = t.legs ?? [];
  const pair = legs.length > 1 || String(t.symbol).includes(",");
  const one = legs[0];
  return (
    <Fragment>
      <tr className={`clickable ${open ? "sel" : ""}`} onClick={onToggle}>
        <td>{when(t.entry_time)}</td><td>{when(t.exit_time)}</td>
        <td><span className="who"><Creature bot={t.bot_id} size={24} mood="still" /> {CREATURES[t.bot_id]?.name ?? t.bot_id}</span></td>
        <td>{pair ? "Gold/silver pair" : <>{t.side === "LONG" ? "▲" : "▼"} {t.volume} {t.symbol}</>}</td>
        <td className="n">{pair ? legs.map((l) => <div key={l.symbol}>{l.symbol.slice(0, 3)} {px(l.entry_price, l.symbol)}</div>) : px(one?.entry_price ?? t.entry_price, t.symbol)}</td>
        <td className="n">{pair ? legs.map((l) => <div key={l.symbol}>{l.symbol.slice(0, 3)} {px(l.exit_price, l.symbol)}</div>) : px(one?.exit_price ?? t.exit_price, t.symbol)}</td>
        <td>{minutes(t.minutes_held)}</td>
        <td className="n"><Pnl v={t.net} />{t.provisional ? "*" : ""}</td>
        <td>{exitKind(t)}</td>
        <td className="muted tiny">{t.config_version ? `v${t.config_version}` : "–"}</td>
      </tr>
      {open && (
        <tr className="detail-row"><td colSpan={10}>
          <div className="trade-detail">
            <div>
              <h3>Legs</h3>
              <Table><thead><tr><th>Symbol</th><th>Side</th><th className="n">Lots</th><th className="n">Entry</th><th className="n">Exit</th><th className="n">Stop-loss</th><th className="n">Take-profit</th><th className="n">Move</th></tr></thead>
                <tbody>{legs.map((l) => <tr key={l.symbol + l.opened_at}><td>{l.symbol}</td><td>{l.side.toLowerCase()}</td><td className="n">{l.volume}</td>
                  <td className="n">{px(l.entry_price, l.symbol)}</td><td className="n">{px(l.exit_price, l.symbol)}</td>
                  <td className="n">{l.sl ? px(l.sl, l.symbol) : "none"}</td><td className="n">{l.tp ? px(l.tp, l.symbol) : "none"}</td>
                  <td className="n">{l.price_move != null ? `${l.price_move > 0 ? "+" : ""}${Number(l.price_move).toFixed(dp(l.symbol))}` : "–"}</td></tr>)}</tbody></Table>
            </div>
            <dl className="rules">
              <dt>Why it entered</dt><dd>{t.entry_rationale || "–"}</dd>
              <dt>Why it exited</dt><dd>{t.exit_rationale || "–"}</dd>
              <dt>Result</dt><dd>gross {money(t.gross, true)}, commission {money(t.commission, true)}, swap {money(t.swap, true)}, net <Pnl v={t.net} />{t.r_multiple != null ? ` (${t.r_multiple}R)` : ""}</dd>
              <dt>Held</dt><dd>{minutes(t.minutes_held)}, from {when(t.entry_time)} to {when(t.exit_time)}{t.config_version ? `, under rules v${t.config_version}` : ""}</dd>
            </dl>
          </div>
        </td></tr>
      )}
    </Fragment>
  );
}

const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Daily net P&L, one square per day for the last eight weeks. Click a day to see only its trades. */
function CalendarStrip({ trades, today, day, onDay }: { trades: Any[]; today: Date; day: string; onDay: (d: string) => void }) {
  const byDay: Record<string, number> = {};
  for (const t of trades) { const k = isoDay(new Date(t.exit_time)); byDay[k] = (byDay[k] ?? 0) + (t.net ?? 0); }
  const days = Array.from({ length: 56 }, (_, i) => new Date(today.getFullYear(), today.getMonth(), today.getDate() - 55 + i));
  const peak = Math.max(1, ...Object.values(byDay).map(Math.abs));
  return (
    <div className="calendar" aria-label="Daily profit and loss, last eight weeks">
      <span className="tiny muted">8 weeks</span>
      {days.map((d) => {
        const k = isoDay(d), v = byDay[k];
        const tone = v == null ? "" : v >= 0 ? "is-up" : "is-down";
        const label = `${d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}: ${v == null ? "no trades" : money(v, true)}`;
        return (
          <Fragment key={k}>
            {d.getDay() === 1 && <span className="gap" />}
            <button className={`${tone} ${v != null && Math.abs(v) >= peak / 2 ? "big" : ""} ${day === k ? "on" : ""}`} title={label} aria-label={label}
                    aria-pressed={day === k} onClick={() => onDay(day === k ? "" : k)} />
          </Fragment>
        );
      })}
      <span className="tiny muted">today</span>
    </div>
  );
}

function ClosedTrades({ initialBot }: { initialBot: string }) {
  const sum = useSummary();
  const [openId, setOpenId] = useState<string | null>(null);
  const [bot, setBot] = useState(initialBot);
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
  const { data: all } = usePoll<Any>(`/api/history${bot ? `?bot=${bot}` : ""}`, 30000);
  const rows: Any[] = data?.trades ?? [];
  const tot = data?.totals;
  const day = since && since === until ? since : "";
  return (
    <>
      <div className="row-between">
        <p className="muted">Closed demo trades, net of spread, commission and swap. R = result divided by what the trade risked at its stop.</p>
        <a className="btn" href={`${apiBase()}/api/history.csv${q ? `?${q}` : ""}`}>Download CSV</a>
      </div>
      <CalendarStrip trades={all?.trades ?? []} today={sum?.server_time ? new Date(sum.server_time) : new Date()} day={day}
                     onDay={(d) => { setSince(d); setUntil(d); }} />
      <form className="inline" onSubmit={(e) => e.preventDefault()}>
        <label>Bot<select value={bot} onChange={(e) => setBot(e.target.value)}><option value="">All</option>
          {BOTS.map((b) => <option key={b} value={b}>{CREATURES[b].name}</option>)}</select></label>
        <label>Metal<select value={symbol} onChange={(e) => setSymbol(e.target.value)}><option value="">Both</option>
          <option value="XAUUSD">Gold</option><option value="XAGUSD">Silver</option></select></label>
        <DateInput label="From" value={since} onChange={setSince} />
        <DateInput label="To" value={until} onChange={setUntil} />
        {(since || until) && <Button type="button" tone="ghost" onClick={() => { setSince(""); setUntil(""); }}>Clear dates</Button>}
      </form>
      {tot && (
        <Stats>
          <Stat label="Trades" value={tot.count} />
          <Stat label="Net" value={<Pnl v={tot.net} />} />
          <Stat label="Won / lost" value={`${tot.wins} / ${tot.losses}`} />
          <Stat label="Win rate" value={tot.count ? `${Math.round((tot.wins / tot.count) * 100)}%` : "–"} />
        </Stats>
      )}
      {rows.length === 0 ? <EmptyState>No closed trades{q ? " for this filter" : " yet"}.</EmptyState> : <>
        <Table className="history">
          <thead><tr><th>Opened</th><th>Closed</th><th>Bot</th><th>Trade</th><th className="n">Entry</th><th className="n">Exit</th>
            <th>Held</th><th className="n">Profit</th><th>Exit</th><th>Rules</th></tr></thead>
          <tbody>{rows.map((t) => <TradeRow key={t.trade_id} t={t} open={openId === t.trade_id} onToggle={() => setOpenId(openId === t.trade_id ? null : t.trade_id)} />)}</tbody>
        </Table>
        <p className="muted tiny">* provisional: costs not yet final in the terminal&apos;s deal history. Click a trade for its stop-loss, take-profit and reasons.</p>
      </>}
    </>
  );
}

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  const tab = sp.get("tab") === "closed" || (sp.get("bot") && sp.get("tab") !== "open") ? "closed" : "open";
  return (
    <>
      <div className="page-head"><div><h1>Trades</h1><p className="lede">What the bots hold now, and everything they have closed.</p></div></div>
      <Tabs tabs={[["open", "Open"], ["closed", "Closed"]]} value={tab} onChange={(t) => router.replace(`/trades?tab=${t}`)} />
      {tab === "open" ? <OpenTrades /> : <ClosedTrades initialBot={sp.get("bot") ?? ""} />}
    </>
  );
}

export default function Trades() {
  return <Suspense fallback={<p className="empty">Loading…</p>}><Inner /></Suspense>;
}
