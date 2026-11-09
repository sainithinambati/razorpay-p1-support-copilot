import { useEffect, useState } from "react";
import { fetchAll } from "../lib";
import Results from "./Results";

export default function Review() {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => {
    fetchAll("v_current_results").then((all) =>
      setRows(all.filter((r) => r.human_review_required || r.validation_status !== "passed")));
  }, []);
  if (!rows) return <p className="muted">Loading...</p>;
  return (
    <>
      <div className="card"><p className="muted">
        Tickets a person must check before anything is sent: critical cases, drafts that failed validation, classifier fallbacks,
        low-confidence classifications and refunds the merchant says are overdue. Click a row to see why.
      </p></div>
      <Results rows={rows} title="Human-review queue" />
    </>
  );
}
