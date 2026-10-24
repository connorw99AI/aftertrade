"use client";
import { ReactNode, useEffect, useRef, useState } from "react";

/** A "more" menu: one button that opens a short list of less common actions. */
export function Menu({ label = "More actions", children }: { label?: string; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button className="btn btn-ghost" aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label} onClick={() => setOpen(!open)}>⋯</button>
      {open && <div className="menu-pop" role="menu">{children(() => setOpen(false))}</div>}
    </div>
  );
}
