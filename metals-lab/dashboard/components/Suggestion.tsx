"use client";
import { CREATURES } from "@/components/Creature";
import { Any, api, confirmAction, when } from "@/lib/api";

const NAMES: Record<string, string> = {
  trade_gold: "Gold", trade_silver: "Silver", gold_windows: "Gold windows", silver_windows: "Silver windows",
};

/** A suggestion's settings change in words: "Gold off", "Silver windows → 07:00-24:00 UK". */
export function changeText(change: Record<string, Any> | null | undefined): string {
  return Object.entries(change ?? {}).map(([k, v]) =>
    k.startsWith("trade_") ? `${NAMES[k]} ${v}` : k.endsWith("_windows") ? `${NAMES[k]} → ${v} UK` : `${k} → ${v}`).join(", ");
}

/** Confirm, then apply the suggestion on the server, which records it as applied. Returns a message, or null if cancelled. */
export async function applySuggestion(bot: string, s: Any, configVersion: number): Promise<string | null> {
  const name = await confirmAction({ title: `Apply this change to ${CREATURES[bot]?.name}?`, confirmLabel: "Apply",
    consequences: [changeText(s.change), `Saved as settings v${configVersion + 1}; the change is logged.`, "The bot must be paused and flat."] });
  if (!name) return null;
  const row = await api.post<Any>(`/api/review/${bot}/apply`,
    { suggestion_id: s.id, change: s.change, operator: name, review_id: s.review_id ?? null, source: s.source ?? null });
  return `Applied to ${CREATURES[bot]?.name} as settings v${row.config_version}.`;
}

/** An applied suggestion, folded to one line; open it to see what it said. */
export function AppliedSuggestion({ s, who }: { s: Any; who?: React.ReactNode }) {
  return (
    <details className="suggestion applied">
      <summary><span className="tick">✓</span> {who}{who ? " · " : ""}Applied in settings v{s.applied.config_version} on {when(s.applied.applied_at)}
        <span className="muted"> · {s.title}</span></summary>
      <p className="muted small">{s.evidence}</p>
      <p>Change: <code>{changeText(s.change)}</code> (by {s.applied.operator})</p>
    </details>
  );
}
