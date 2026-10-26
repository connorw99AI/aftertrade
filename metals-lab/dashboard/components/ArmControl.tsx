"use client";
import { useState } from "react";
import { Button } from "@/components/ui";
import { Any, api, confirmAction, money } from "@/lib/api";

/** Arm or disarm automatic DEMO trading. Arming re-checks the demo account, shows what it will do and needs the same
 *  typed phrase as the command line; it only works from the PC running Metal Lab. */
export function ArmControl({ armed, onDone }: { armed: boolean; onDone?: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function arm() {
    setMsg(null); setBusy(true);
    try {
      const pv = await api.get<Any>("/api/arm/preview");
      if (pv.problems.length) { setMsg(`Cannot arm: ${pv.problems.join("; ")}`); return; }
      const name = await confirmAction({
        title: "Arm automatic demo trading?", confirmLabel: "Arm demo trading", tone: "go", typed: pv.phrase,
        consequences: [
          `Verified demo account on ${pv.account.server} (${pv.account.margin_mode.toLowerCase()}).`,
          pv.snapshot_exists ? `Starting balance already recorded: ${money(pv.starting_balance)}; each of the ${pv.bots} bots keeps its share.`
            : `Starting balance recorded now: ${money(pv.starting_balance)}. Each of the ${pv.bots} bots gets ${money(pv.per_bot)}.`,
          `${pv.running} bot(s) marked running start trading on the demo account; the rest wait until you press Start.`,
          `Type ${pv.phrase} to confirm. Logged with your name.`,
        ] });
      if (!name) return;
      const r = await api.post<Any>("/api/arm", { phrase: pv.phrase, operator: name });
      setMsg(`Armed. Each bot's share: ${money(r.per_bot_allocation)}.`);
      onDone?.();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  async function disarm() {
    setMsg(null);
    const name = await confirmAction({ title: "Disarm automatic trading?", confirmLabel: "Disarm", tone: "pause",
      consequences: ["No bot opens new trades until you arm again.", "Open trades stay as they are; close them on the Trades page if you want them closed."] });
    if (!name) return;
    setBusy(true);
    try { await api.post("/api/disarm", { reason: "disarmed from the dashboard", operator: name }); setMsg("Disarmed."); onDone?.(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  return (
    <span className="arm-control">
      {armed ? <Button tone="pause" disabled={busy} onClick={disarm}>Disarm</Button>
        : <Button tone="go" disabled={busy} onClick={arm}>Arm demo trading</Button>}
      {msg && <span className="small muted"> {msg}</span>}
    </span>
  );
}
