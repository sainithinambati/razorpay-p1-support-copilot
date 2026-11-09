import { useEffect, useMemo, useState } from "react";
import { ACTIONS, CATEGORIES, RISKS, TEAMS, fetchAll } from "../lib";
import { Badge, TicketDetail } from "../components";

export default function Results({ rows: given, title }: { rows?: any[]; title?: string }) {
  const [rows, setRows] = useState<any[]>(given || []);
  const [f, setF] = useState({ category: "", action: "", team: "", risk: "", validation: "", lang: "", q: "" });
  const [open, setOpen] = useState<any>(null);
  useEffect(() => { if (!given) fetchAll("v_current_results").then(setRows); else setRows(given); }, [given]);

  const shown = useMemo(() => rows.filter((r) =>
    (!f.category || r.category === f.category) && (!f.action || r.action === f.action) &&
    (!f.team || (f.team === "(none)" ? !r.escalation_team : r.escalation_team === f.team)) &&
    (!f.risk || r.risk_level === f.risk) && (!f.validation || r.validation_status === f.validation) &&
    (!f.lang || r.language_style === f.lang) &&
    (!f.q || `${r.ticket_id} ${r.merchant_id} ${r.merchant_message}`.toLowerCase().includes(f.q.toLowerCase()))), [rows, f]);
  const sel = (k: keyof typeof f, opts: string[], label: string) => (
    <select value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })}>
      <option value="">{label}: all</option>{opts.map((o) => <option key={o}>{o}</option>)}
    </select>);

  return (
    <section className="card">
      <div className="row between">
        <h2>{title || "Mandatory result table"} <small className="muted">({shown.length} of {rows.length})</small></h2>
        {!given && <div className="row"><a className="btn" href="/api/export?format=csv">Export CSV</a><a className="btn" href="/api/export?format=xlsx">Export XLSX</a></div>}
      </div>
      <div className="row wrap">
        {sel("category", CATEGORIES, "Category")}{sel("action", ACTIONS, "Action")}{sel("team", [...TEAMS, "(none)"], "Team")}
        {sel("risk", RISKS, "Risk")}{sel("validation", ["passed", "failed_fallback", "processing_failed"], "Validation")}
        {sel("lang", ["English", "Hinglish"], "Language")}
        <input placeholder="Search ticket, merchant or text" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
      </div>
      <div className="tablewrap">
        <table className="results">
          <thead><tr><th>ticket_id</th><th>category</th><th>action</th><th>escalation_team</th><th>risk_level</th><th>drafted_reply</th><th>checks</th></tr></thead>
          <tbody>{shown.map((r) => (
            <tr key={r.ticket_id + (r.run_id || "")} onClick={() => setOpen(r)} className="click">
              <td><b>{r.ticket_id}</b><br /><small className="muted">{r.merchant_id || "no ID"}</small></td>
              <td>{r.category}</td><td><Badge v={r.action} /></td><td>{r.escalation_team || ""}</td><td><Badge v={r.risk_level} /></td>
              <td className="reply">{r.drafted_reply}</td>
              <td><Badge v={r.validation_status} />{r.human_review_required && <div><small className="warnline">review</small></div>}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
      {open && <TicketDetail r={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
