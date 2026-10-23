"use client";
import { useState } from "react";
import { Tabs } from "@/components/Bits";
import Auditor from "../auditor/page";
import Research from "../research/page";
import Risk from "../risk/page";
import Settings from "../settings/page";

export default function System() {
  const [tab, setTab] = useState("risk");
  return (
    <>
      <div className="page-head"><h1>System</h1><p className="muted">Risk settings and safety controls, the auditor&apos;s rules, the MT5 connection and research records.</p></div>
      <Tabs tabs={[["risk", "Risk & safety"], ["auditor", "Auditor"], ["mt5", "MT5 connection"], ["research", "Research"]]} value={tab} onChange={setTab} />
      <div className="system-body">
        {tab === "risk" && <Risk />}
        {tab === "auditor" && <Auditor />}
        {tab === "mt5" && <Settings />}
        {tab === "research" && <Research />}
      </div>
    </>
  );
}
