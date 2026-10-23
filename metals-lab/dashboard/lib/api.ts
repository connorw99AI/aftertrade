export function apiBase(): string {
  if (typeof window === "undefined") return "";
  const env = process.env.NEXT_PUBLIC_API_BASE;
  if (env) return env;
  // `next dev` runs on :3000; the Python API runs on :8765. The built dashboard is served by the API itself.
  return window.location.port === "3000" ? "http://127.0.0.1:8765" : "";
}

function token(): string | null {
  try {
    return window.localStorage.getItem("metals-lab-token");
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const t = token();
  if (t) headers["X-API-Token"] = t;
  const r = await fetch(apiBase() + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* keep status text */
    }
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

export const api = {
  get: <T,>(p: string) => call<T>("GET", p),
  post: <T,>(p: string, b: unknown = {}) => call<T>("POST", p, b),
  put: <T,>(p: string, b: unknown) => call<T>("PUT", p, b),
};

export function money(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!signed) return (v < 0 ? "−" : "") + s;
  return (v > 0 ? "+" : v < 0 ? "−" : "±") + s;
}

export function pct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Any = any;

export function operatorName(): string {
  try {
    return window.localStorage.getItem("metals-lab-operator") || "";
  } catch {
    return "";
  }
}

/** Ask for confirmation and the operator's name (remembered) before an action that changes trading. */
export function confirmAction(question: string): string | null {
  if (!window.confirm(question)) return null;
  let name = operatorName();
  if (!name) {
    name = (window.prompt("Your name for the audit log") || "").trim();
    if (!name) return null;
    try { window.localStorage.setItem("metals-lab-operator", name); } catch { /* not persisted */ }
  }
  return name;
}

export function minutes(m: number | null | undefined): string {
  if (m === null || m === undefined) return "–";
  if (m < 60) return `${Math.round(m)}m`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ${Math.round(m % 60)}m` : `${Math.floor(h / 24)}d ${h % 24}h`;
}
