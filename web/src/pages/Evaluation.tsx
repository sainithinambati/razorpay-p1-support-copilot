import { useEffect, useState } from "react";
import { fetchAll, pct } from "../lib";
import { Badge, Stat } from "../components";

export default function Evaluation() {
  const [rows, setRows] = useState<any[]>([]);
  const [all, setAll] = useState<any[]>([]);
  useEffect(() => {
    fetchAll("v_eval_results").then(setRows);
    fetchAll("v_current_results", "ticket_id,action").then(setAll);
  }, []);
  const done = rows.filter((r) => r.actual_action);
  const n = done.length;
  const c = (k: string) => done.filter((r) => r[k]).length;
  const predAuto = done.filter((r) => r.actual_action === "AUTO_RESOLVE");
  const autoCorrect = predAuto.filter((r) => r.expected_action === "AUTO_RESOLVE").length;
  const critical = done.filter((r) => r.critical_auto_resolved).length;
  const allAuto = all.filter((r) => r.action === "AUTO_RESOLVE").length;
  const exact = done.filter((r) => r.category_match && r.action_match && r.team_match).length;

  return (
    <>
      <section className="card">
        <h2>Evaluation on the manually labelled set</h2>
        <p className="muted">{rows.length} tickets labelled by the builder from the KB rules. These are <b>not</b> Razorpay's hidden answers, and the labels were
          written by the same person who wrote the rules, so agreement here is an upper bound. Accuracy against the hidden key cannot be measured here.</p>
      </section>
      <section className="stats">
        <Stat label="Labelled cases with a result" value={`${n} / ${rows.length}`} />
        <Stat label="Category accuracy" value={pct(c("category_match"), n)} />
        <Stat label="Action accuracy" value={pct(c("action_match"), n)} />
        <Stat label="Team accuracy" value={pct(c("team_match"), n)} />
        <Stat label="Risk-level accuracy" value={pct(c("risk_match"), n)} />
        <Stat label="All of category, action, team right" value={pct(exact, n)} />
        <Stat label="Critical cases auto-resolved" value={critical} sub="target 0" />
        <Stat label="Auto-resolve precision (labelled)" value={pct(autoCorrect, predAuto.length)} sub={`${autoCorrect} of ${predAuto.length}`} />
        <Stat label="Auto-resolve share (all 150)" value={pct(allAuto, all.length)} sub={`${allAuto} of ${all.length}`} />
      </section>
      <section className="card">
        <div className="tablewrap"><table>
          <thead><tr><th>Ticket</th><th>Case</th><th>Expected category</th><th>Actual</th><th>Expected action</th><th>Actual</th><th>Expected team</th><th>Actual</th><th>Risk exp / act</th><th>Draft safe</th><th>KB</th><th>Failure reason</th></tr></thead>
          <tbody>{rows.map((r) => {
            const miss = r.actual_action && !(r.category_match && r.action_match && r.team_match);
            return (
              <tr key={r.ticket_id} className={miss ? "missrow" : ""}>
                <td><b>{r.ticket_id}</b></td><td>{r.case_type}</td>
                <td>{r.expected_category}</td><td className={r.category_match ? "ok" : "bad"}>{r.actual_category || "not run"}</td>
                <td>{r.expected_action}</td><td className={r.action_match ? "ok" : "bad"}>{r.actual_action || "-"}</td>
                <td>{r.expected_team || "-"}</td><td className={r.team_match ? "ok" : "bad"}>{r.actual_team || "-"}</td>
                <td>{r.expected_risk_level} / <Badge v={r.actual_risk_level} /></td>
                <td>{r.validation_status ? (r.validation_status === "passed" ? "Yes" : "Blocked, fallback used") : "-"}</td>
                <td><small>{(r.actual_kb_ids || []).join(", ")}</small></td>
                <td><small>{r.failure_reason || (miss ? "Mismatch with label" : "")}</small></td>
              </tr>);
          })}</tbody>
        </table></div>
      </section>
    </>
  );
}
