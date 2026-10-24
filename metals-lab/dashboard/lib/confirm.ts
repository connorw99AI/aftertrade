/* In-app confirmation for anything that changes trading. Replaces the browser's confirm and prompt boxes.
   The dialog itself is components/ui/Dialog.tsx, mounted once by the Shell; this file is only the request queue. */

export type ConfirmRequest = {
  title: string;
  /** What will happen, one line each. Shown as a list so the consequence is read before the button. */
  consequences?: string[];
  body?: string;
  confirmLabel?: string;
  tone?: "go" | "pause" | "danger" | "primary";
  /** A word the operator must type, e.g. "KILL". */
  typed?: string;
};

type Pending = ConfirmRequest & { resolve: (name: string | null) => void };
let listener: ((p: Pending | null) => void) | null = null;
let queue: Pending[] = [];

export function subscribeConfirm(fn: (p: Pending | null) => void) {
  listener = fn;
  fn(queue[0] ?? null);
  return () => { if (listener === fn) listener = null; };
}

export function settleConfirm(name: string | null) {
  const p = queue.shift();
  p?.resolve(name);
  listener?.(queue[0] ?? null);
}

const KEY = "metals-lab-operator";
export function operatorName(): string {
  try { return window.localStorage.getItem(KEY) || ""; } catch { return ""; }
}
export function rememberOperator(name: string) {
  try { window.localStorage.setItem(KEY, name); } catch { /* not persisted */ }
}

/** Ask before an action that changes trading. Resolves to the operator's name (remembered), or null if cancelled. */
export function confirmAction(req: string | ConfirmRequest): Promise<string | null> {
  const r: ConfirmRequest = typeof req === "string" ? { title: req } : req;
  return new Promise((resolve) => {
    if (!listener) { resolve(null); return; } // no dialog mounted: never act without asking
    queue.push({ ...r, resolve });
    if (queue.length === 1) listener(queue[0]);
  });
}
