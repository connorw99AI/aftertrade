"use client";
import { Any, when } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Research() {
  const { data } = usePoll<Any>("/api/research/experiments?limit=300", 20000);
  const ex = data?.experiments ?? [];
  return (
    <>
      <h1>Research and tuning</h1>
      <p className="muted">Every trial is recorded, including failures, so multiple-testing corrections use the real number of attempts.
        Run studies from the command line: <code>metals-lab research --strategy S08</code>. The final holdout can be evaluated once per strategy and dataset.</p>
      <h2>Holdout usage</h2>
      {!data?.holdout_usage?.length ? <p className="empty">No holdout has been touched.</p> : (
        <table><thead><tr><th>Strategy</th><th>Dataset</th><th>Used</th><th>Params</th></tr></thead>
          <tbody>{data.holdout_usage.map((h: Any) => <tr key={h.strategy_id + h.dataset_key}><td>{h.strategy_id}</td><td>{h.dataset_key}</td><td>{when(h.used_at)}</td><td className="muted">{h.params_json}</td></tr>)}</tbody></table>)}
      <h2>Experiment history</h2>
      {!ex.length ? <p className="empty">No experiments yet.</p> : (
        <div className="table-wrap"><table><thead><tr><th>When</th><th>Strategy</th><th>Kind</th><th>Data</th><th>Period</th><th>Status</th><th className="n">Trades</th><th className="n">Sharpe</th><th>Params</th></tr></thead>
          <tbody>{ex.map((e: Any) => <tr key={e.id}><td>{when(e.created_at)}</td><td>{e.strategy_id}</td><td>{e.kind}</td><td>{e.dataset_key}</td><td className="muted">{e.period}</td>
            <td>{e.status}</td><td className="n">{e.metrics?.n_trades ?? "–"}</td><td className="n">{e.metrics?.sharpe_daily_ann ?? "–"}</td><td className="muted">{JSON.stringify(e.params)}</td></tr>)}</tbody></table></div>)}
    </>
  );
}
