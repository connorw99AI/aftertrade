"use client";
import { useState } from "react";
import { BOTS, Creature, CREATURES } from "@/components/Creature";
import { EmptyState } from "@/components/ui";
import { Any, money, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const STAGE: Record<string, [string, string]> = {
  skipped_sizing: ["skipped at sizing", "pill-kill"], auditor_held: ["held by the auditor", "pill-pause"], risk_rejected: ["refused by risk", "pill-kill"],
  approved: ["approved", "pill-run"], other: ["", ""],
};

const SIDE: Record<string, string> = { OPEN_LONG: "open a long", OPEN_SHORT: "open a short", CLOSE_LONG: "close its long", CLOSE_SHORT: "close its short" };

function eventText(e: Any): string {
  const p = e.payload || {};
  if (p.net !== undefined) return `net ${money(Number(p.net), true)}`;
  if (p.reason) return String(p.reason);
  return Object.entries(p).slice(0, 3).map(([k, v]) => `${k.replace(/_/g, " ")} ${typeof v === "object" ? "…" : v}`).join(", ");
}

/** Every decision and system event, newest first, filterable by bot. */
export default function Log() {
  const [bot, setBot] = useState("");
  const { data } = usePoll<Any>(`/api/activity${bot ? `?bot=${bot}` : ""}`, 5000);
  const feed: Any[] = (data?.feed ?? []).filter((e: Any) => e.kind !== "event" || e.event !== "TRADE_FINALIZED");
  return (
    <>
      <div className="row-between">
        <p className="muted">Every signal a bot produced and what happened to it, plus system events. Why a bot did not trade is on its own page.</p>
        <label>Bot<select value={bot} onChange={(e) => setBot(e.target.value)}>
          <option value="">All bots</option>{BOTS.map((b) => <option key={b} value={b}>{CREATURES[b].name}</option>)}</select></label>
      </div>
      {data && feed.length === 0 ? <EmptyState>Nothing logged yet.</EmptyState> : (
        <ul className="feed">
          {feed.map((e: Any, i: number) => e.kind === "decision" ? (
            <li key={i}>
              <span className="when">{when(e.ts)}</span>
              <span className="who"><Creature bot={e.bot_id} size={22} mood="still" /> {CREATURES[e.bot_id]?.name}</span>
              <span>Wanted to {SIDE[e.intent] ?? String(e.intent || "").replace("_", " ").toLowerCase()}{e.role ? ` on ${e.role === "GOLD" ? "gold" : "silver"}` : ""}: {e.rationale}</span>
              <span className={`pill ${STAGE[e.stage]?.[1] ?? ""}`} title={e.outcome}>{STAGE[e.stage]?.[0] || String(e.outcome || "").toLowerCase()}{e.mode === "SHADOW" ? " (shadow)" : ""}</span>
            </li>
          ) : (
            <li key={i} className="ev">
              <span className="when">{when(e.ts)}</span>
              <span className="who">{e.bot_id ? <><Creature bot={e.bot_id} size={22} mood="still" /> {CREATURES[e.bot_id]?.name}</> : "System"}</span>
              <span>{e.event.replace(/_/g, " ").toLowerCase()} <span className="muted">{eventText(e)}</span></span>
              <span />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
