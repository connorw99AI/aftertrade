"use client";
import { useState } from "react";
import { Creature, CREATURES } from "./Creature";
import { Any, when } from "@/lib/api";

const VERDICT: Record<string, { text: string; tone: string }> = {
  ENTRY_LONG: { text: "Entry: long", tone: "v-entry" },
  ENTRY_SHORT: { text: "Entry: short", tone: "v-entry" },
  NO_ENTRY: { text: "No entry", tone: "v-none" },
  IN_TRADE_LONG: { text: "In a long trade", tone: "v-trade" },
  IN_TRADE_SHORT: { text: "In a short trade", tone: "v-trade" },
  WAITING_DATA: { text: "Waiting for data", tone: "v-wait" },
};

function Checks({ title, items }: { title: string; items: Any[] }) {
  if (!items?.length) return null;
  const met = items.filter((c) => c.ok).length;
  return (
    <div className="checks">
      <div className="checks-title">{title} <span className="muted">{met} of {items.length}</span></div>
      <ul>
        {items.map((c, i) => (
          <li key={i} className={c.ok ? "ok" : "no"}>
            <span className="mark" aria-label={c.ok ? "met" : "not met"}>{c.ok ? "✓" : "✗"}</span>
            <span>{c.label}{c.detail ? <span className="muted">: {c.detail}</span> : null}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One bot's view of one metal. */
export function RoleThinking({ bot, th, role, compact = false }: { bot: string; th: Any; role: string; compact?: boolean }) {
  const [open, setOpen] = useState(!compact);
  const r = th?.roles?.[role];
  if (!r) return null;
  const v = VERDICT[r.verdict] ?? { text: r.verdict, tone: "v-none" };
  return (
    <div className={`thinking ${v.tone}`}>
      <button className="thinking-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        {compact && <Creature bot={bot} size={28} mood={th.bot_status === "RETIRED" ? "asleep" : th.bot_status === "RUNNING" ? "idle" : "still"} />}
        <span className="thinking-who">
          {compact && <b>{CREATURES[bot]?.name} <span className="muted">{bot}, {th.timeframe}{th.bot_status !== "RUNNING" ? `, ${th.bot_status.toLowerCase()}` : ""}</span></b>}
          <span className="thinking-sum">{r.summary}</span>
        </span>
        <span className={`verdict ${v.tone}`}>{v.text}</span>
      </button>
      {open && !("waiting" in r) && (
        <div className="thinking-body">
          <Checks title="Filters (must all pass)" items={r.filters} />
          <div className="checks-sides">
            <Checks title="Long entry" items={r.long} />
            <Checks title="Short entry" items={r.short} />
          </div>
          <p className="muted small">As of the {th.timeframe} bar closing {when(th.as_of)}. {th.note}</p>
        </div>
      )}
    </div>
  );
}
