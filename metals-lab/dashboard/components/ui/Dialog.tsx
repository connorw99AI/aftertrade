"use client";
import { useEffect, useRef, useState } from "react";
import { ConfirmRequest, operatorName, rememberOperator, settleConfirm, subscribeConfirm } from "@/lib/confirm";
import { Button } from "./Button";

/** The confirmation dialog: says what will happen, asks for (and remembers) the operator's name, and can require a typed word. */
export function DialogHost() {
  const [req, setReq] = useState<ConfirmRequest | null>(null);
  const [name, setName] = useState("");
  const [typed, setTyped] = useState("");
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => subscribeConfirm((p) => { setReq(p); setName(operatorName()); setTyped(""); }), []);
  useEffect(() => { if (req) setTimeout(() => first.current?.focus(), 0); }, [req]);
  if (!req) return null;
  const ok = name.trim() !== "" && (!req.typed || typed.trim().toUpperCase() === req.typed.toUpperCase());
  const done = (yes: boolean) => {
    if (yes) rememberOperator(name.trim());
    settleConfirm(yes ? name.trim() : null);
  };
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) done(false); }}>
      <form className={`dialog tone-${req.tone ?? "primary"}`} role="alertdialog" aria-modal="true" aria-labelledby="dlg-title"
            onSubmit={(e) => { e.preventDefault(); if (ok) done(true); }} onKeyDown={(e) => { if (e.key === "Escape") done(false); }}>
        <h2 id="dlg-title">{req.title}</h2>
        {req.body && <p className="muted">{req.body}</p>}
        {req.consequences?.length ? <ul className="consequences">{req.consequences.map((c) => <li key={c}>{c}</li>)}</ul> : null}
        <label>Your name, for the audit log
          <input ref={req.typed ? undefined : first} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
        </label>
        {req.typed && (
          <label>Type {req.typed} to confirm
            <input ref={first} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </label>
        )}
        <div className="actions">
          <Button type="button" tone="ghost" onClick={() => done(false)}>Cancel</Button>
          <Button type="submit" tone={req.tone === "danger" ? "danger" : req.tone === "go" ? "go" : req.tone === "pause" ? "pause" : "primary"} disabled={!ok}>
            {req.confirmLabel ?? "Confirm"}
          </Button>
        </div>
      </form>
    </div>
  );
}
