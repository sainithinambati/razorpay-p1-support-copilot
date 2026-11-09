import { useEffect, useState } from "react";
import { fetchAll } from "../lib";
import Results from "./Results";

export default function Review() {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => {
    fetchAll("v_current_results").then((all) =>
      setRows(all.filter((r) => r.human_review_required || r.validation_status !== "passed")));
  }, []);
  if (!rows) return <p className="muted">Loading.</p>;
  const crit = rows.filter((r) => r.risk_level === "Critical").length;
  const blocked = rows.filter((r) => r.validation_status === "failed_fallback").length;
  return (
    <>
      <div className="reasons">
        <div className="reason t-crit"><b>{crit}</b> critical cases (legal, security, staff misconduct). A specialist team owns the reply.</div>
        <div className="reason t-high"><b>{blocked}</b> drafts the safety checker stopped. A safe fallback is in place.</div>
        <div className="reason t-ask"><b>{rows.filter((r) => r.risk_level !== "Critical" && r.validation_status !== "failed_fallback").length}</b> other flags, such as overdue refunds or low confidence. Open a ticket to see why.</div>
      </div>
      <Results rows={rows} title="Flagged tickets" />
    </>
  );
}
