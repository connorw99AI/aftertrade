"use client";
import { useEffect, useState } from "react";
import { apiBase } from "./api";

export type Ticks = Record<string, { bid: number; ask: number; symbol: string }>;

/** Live bid/ask for both metals from the server-sent event stream (about once a second). */
export function useTicks(): { ticks: Ticks; live: boolean } {
  const [ticks, setTicks] = useState<Ticks>({});
  const [live, setLive] = useState(false);
  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const open = () => {
      es = new EventSource(`${apiBase()}/api/stream?interval_ms=1000`);
      es.onmessage = (m) => {
        try {
          const d = JSON.parse(m.data);
          setTicks(d.ticks || {});
          setLive(true);
        } catch { /* ignore a malformed frame */ }
      };
      es.onerror = () => {
        setLive(false);
        es?.close();
        retry = setTimeout(open, 3000);
      };
    };
    open();
    return () => { es?.close(); if (retry) clearTimeout(retry); };
  }, []);
  return { ticks, live };
}
