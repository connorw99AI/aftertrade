"use client";
import Link from "next/link";
import { useState } from "react";
import { Creature, CREATURES } from "@/components/Creature";
import { Any, api, confirmAction, minutes, money } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Positions() {
  const { data, error, reload } = usePoll<Any>("/api/positions", 2000);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rows: Any[] = data?.positions ?? [];
  const total = rows.reduce((s, p) => s + (p.profit ?? 0), 0);
  const bots = Array.from(new Set(rows.map((p) => p.bot_id)));

  async function close(scope: "position" | "bot" | "all", target: string | null, what: string) {
    const name = confirmAction(`Close ${what} at market now? This goes through the auditor and risk checks like any exit.`);
    if (!name) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.post<Any>("/api/positions/close", { scope, target, operator: name });
      setMsg(r.results.map((x: Any) => `${x.position}: ${x.outcome}`).join(" · "));
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head row-between">
        <div>
          <h1>Open positions</h1>
          <p className="muted">Every trade the bots hold, live from the terminal. &quot;Protected&quot; means the stop-loss and take-profit in MT5 match what the bot opened with; the auditor checks and restores them every cycle.</p>
        </div>
        {rows.length > 0 && <button className="btn-stop" disabled={busy} onClick={() => close("all", null, `ALL ${rows.length} positions`)}>Close everything</button>}
      </div>
      {msg && <div className="warn">{msg}</div>}
      {error && <div className="warn danger">{error}</div>}
      {rows.length === 0 ? <p className="empty">No open positions.</p> : (
        <>
          <div className="metrics">
            <div className="metric"><span>Open trades</span><b>{rows.length}</b></div>
            <div className="metric"><span>Open P&amp;L</span><b className={total >= 0 ? "gain" : "loss"}>{money(total, true)}</b></div>
            <div className="metric"><span>Bots in a trade</span><b>{bots.length}</b></div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Bot</th><th>Trade</th><th className="n">Entry</th><th className="n">Now</th><th className="n">P&amp;L</th>
                <th className="n">Stop (to it)</th><th className="n">Target (to it)</th><th>Open for</th><th>Protected</th><th /></tr></thead>
              <tbody>
                {rows.map((p) => {
                  const d = p.symbol.startsWith("XAG") ? 3 : 2;
                  return (
                    <tr key={p.position_key}>
                      <td><Link href={`/bot?id=${p.bot_id}`} className="who"><Creature bot={p.bot_id} size={28} mood="busy" /> {CREATURES[p.bot_id]?.name}</Link></td>
                      <td><b className={p.side === "LONG" ? "gain" : "loss"}>{p.side === "LONG" ? "▲ Long" : "▼ Short"}</b> {p.volume_open} {p.symbol}{p.group_id && p.group_id.startsWith("S10") ? <span className="muted"> (pair)</span> : null}</td>
                      <td className="n num">{Number(p.entry_price).toFixed(d)}</td>
                      <td className="n num">{p.price != null ? Number(p.price).toFixed(d) : "–"}</td>
                      <td className={`n num ${(p.profit ?? 0) >= 0 ? "gain" : "loss"}`}>{money(p.profit, true)}</td>
                      <td className="n num">{p.sl ? Number(p.sl).toFixed(d) : "none"}{p.to_sl ? <span className="muted"> ({money(p.to_sl.money, true)})</span> : null}</td>
                      <td className="n num">{p.tp ? Number(p.tp).toFixed(d) : "none"}{p.to_tp ? <span className="muted"> ({money(p.to_tp.money, true)})</span> : null}</td>
                      <td>{minutes(p.minutes_open)}</td>
                      <td>{p.protection_ok ? <span className="gain">✓ yes</span> : <span className="loss" title={`In MT5: SL ${p.terminal_sl ?? "none"}, TP ${p.terminal_tp ?? "none"}`}>✗ being restored</span>}</td>
                      <td className="actions-cell">
                        <button className="btn-stop small-btn" disabled={busy} onClick={() => close("position", p.position_key, `${CREATURES[p.bot_id]?.name}'s ${p.side.toLowerCase()} ${p.symbol}`)}>Close</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {bots.length > 1 && (
            <div className="actions" style={{ marginTop: 12 }}>
              <span className="muted">Close all of one bot&apos;s trades:</span>
              {bots.map((b) => <button key={b} disabled={busy} onClick={() => close("bot", b, `all of ${CREATURES[b]?.name}'s trades`)}>{CREATURES[b]?.name}</button>)}
            </div>
          )}
        </>
      )}
    </>
  );
}
