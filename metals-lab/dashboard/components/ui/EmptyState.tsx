import { ReactNode } from "react";
import { Creature } from "../Creature";

/** The Assessor speaks for empty states, so "nothing here" always sounds like the same voice. */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="empty-state">
      <Creature bot="ASSESSOR" size={44} label="The Assessor" />
      <div className="say">{children}</div>
    </div>
  );
}
