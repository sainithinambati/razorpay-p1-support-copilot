import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { fmtTime, sb } from "../lib";

const SUMMARY = "blocked_id,analysis_id,run_id,ticket_id,unsafe_statement,reason,violated_kb_id,fallback_action,required_team,created_at";

export default function Blocked({ session }: { session: Session | null }) {
  const [rows, setRows] = useState<any[]>([]);
  const [allRuns, setAllRuns] = useState(false);
  const [latest, setLatest] = useState<string | null>(null);
  useEffect(() => {
    sb.from("v_run_progress").select("run_id").eq("run_type", "batch").order("created_at", { ascending: false }).limit(1)
      .then(({ data }) => setLatest(data?.[0]?.run_id || null));
  }, []);
  useEffect(() => {
    let q = sb.from("ai_blocked_drafts").select(session ? "*" : SUMMARY).order("created_at", { ascending: false }).limit(500);
    if (!allRuns && latest) q = q.eq("run_id", latest);
    q.then(({ data }) => setRows(data || []));
  }, [session, allRuns, latest]);
  return (
    <section className="card">
      <h2>Replies that must not be sent</h2>
      <p className="muted">Drafts stopped by output validation. The merchant never sees these; a safe fallback was used and a person must review.
        {session ? "" : " Sign in to see the full failed drafts (restricted)."}</p>
      <label className="row" style={{ textTransform: "none", fontSize: 13 }}>
        <input type="checkbox" checked={allRuns} onChange={(e) => setAllRuns(e.target.checked)} /> Show all runs (default: latest batch run only)
      </label>
      {rows.length === 0 && <p>No drafts were blocked {allRuns ? "in any run" : "in the latest run"}.</p>}
      <table>
        <thead><tr><th>Ticket</th><th>Unsafe statement</th><th>Why it was unsafe</th><th>KB</th><th>Fallback action</th><th>Human team</th><th>When</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.blocked_id}>
            <td><b>{r.ticket_id}</b></td>
            <td className="reply">{r.unsafe_statement}{r.failed_draft && <details><summary>Full failed draft</summary><pre>{r.failed_draft}</pre></details>}</td>
            <td>{r.reason}</td><td>{r.violated_kb_id}</td><td>{r.fallback_action}</td><td>{r.required_team}</td><td>{fmtTime(r.created_at)}</td>
          </tr>))}
        </tbody>
      </table>
    </section>
  );
}
