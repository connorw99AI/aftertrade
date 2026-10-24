"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Tabs } from "@/components/Bits";
import Auditor from "@/components/system/Auditor";
import Connection from "@/components/system/Connection";
import Experiments from "@/components/system/Experiments";
import Log from "@/components/system/Log";
import Risk from "@/components/system/Risk";

const TABS: [string, string][] = [["risk", "Risk & safety"], ["auditor", "Auditor"], ["mt5", "MT5 connection"], ["log", "Log"], ["experiments", "Experiments"]];

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  const tab = TABS.some(([k]) => k === sp.get("tab")) ? sp.get("tab")! : "risk";
  return (
    <>
      <div className="page-head"><div><h1>System</h1>
        <p className="lede">Risk controls and limits, the market auditor, the MT5 connection, the full decision log and research records.</p></div></div>
      <Tabs tabs={TABS} value={tab} onChange={(t) => router.replace(`/system?tab=${t}`)} />
      {tab === "risk" && <Risk />}
      {tab === "auditor" && <Auditor />}
      {tab === "mt5" && <Connection />}
      {tab === "log" && <Log />}
      {tab === "experiments" && <Experiments />}
    </>
  );
}

export default function System() {
  return <Suspense fallback={<p className="empty">Loading…</p>}><Inner /></Suspense>;
}
