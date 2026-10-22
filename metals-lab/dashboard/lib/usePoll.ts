"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export function usePoll<T>(path: string | null, ms = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api.get<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    load();
    const id = ms > 0 ? setInterval(load, ms) : undefined;
    return () => {
      alive.current = false;
      if (id) clearInterval(id);
    };
  }, [load, ms]);
  return { data, error, reload: load };
}
