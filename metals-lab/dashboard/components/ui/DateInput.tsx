"use client";
import { useEffect, useState } from "react";

const toUk = (iso: string) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "");
function toIso(uk: string): string | null {
  const m = uk.trim().match(/^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/);
  if (!m) return null;
  const [d, mo, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  return dt.toISOString().slice(0, 10);
}

/** A day-first date field (dd/mm/yyyy) whatever the browser's locale. Value in and out is ISO (yyyy-mm-dd), "" for none. */
export function DateInput({ value, onChange, label }: { value: string; onChange: (iso: string) => void; label: string }) {
  const [text, setText] = useState(toUk(value));
  useEffect(() => setText(toUk(value)), [value]);
  const iso = toIso(text);
  const bad = text.trim() !== "" && !iso;
  return (
    <label>{label}
      <input className={`date-input ${bad ? "bad" : ""}`} inputMode="numeric" placeholder="dd/mm/yyyy" value={text} aria-invalid={bad}
             onChange={(e) => { setText(e.target.value); const v = toIso(e.target.value); if (v) onChange(v); else if (!e.target.value.trim()) onChange(""); }} />
    </label>
  );
}
