import { useEffect, useMemo, useState } from "react";
import { ACTIONS, CATEGORIES, RISKS, TEAMS, fetchAll } from "../lib";
import { Badge, LABELS, TicketDetail } from "../components";

const EMPTY = { category: "", action: "", team: "", risk: "", validation: "", lang: "", q: "" };

export default function Results({ rows: given, title }: { rows?: any[]; title?: string }) {
  const [rows, setRows] = useState<any[]>(given || []);
  const [loading, setLoading] = useState(!given);
  const [f, setF] = useState(EMPTY);
  const [open, setOpen] = useState<any>(null);
  useEffect(() => {
    if (given) { setRows(given); return; }
    fetchAll("v_current_results").then((r) => { setRows(r); setLoading(false); });
  }, [given]);

  const shown = useMemo(() => rows.filter((r) =>
    (!f.category || r.category === f.category) && (!f.action || r.action === f.action) &&
    (!f.team || (f.team === "none" ? !r.escalation_team : r.escalation_team === f.team)) &&
    (!f.risk || r.risk_level === f.risk) && (!f.validation || r.validation_status === f.validation) &&
    (!f.lang || r.language_style === f.lang) &&
    (!f.q || `${r.ticket_id} ${r.merchant_id} ${r.merchant_message}`.toLowerCase().includes(f.q.toLowerCase()))), [rows, f]);
  const active = Object.entries(f).filter(([, v]) => v).length;
  const sel = (k: keyof typeof f, opts: [string, string][], label: string) => (
    <select value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} aria-label={label} className={f[k] ? "set" : ""}>
      <option value="">{label}</option>{opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>);
  const plain = (xs: string[]) => xs.map((x) => [x, LABELS[x] || x] as [string, string]);

  return (
    <section className="panel">
      <div className="row between wrap">
        <h2>{title || "Submission table"} <span className="muted small">showing {shown.length} of {rows.length}</span></h2>
        {!given && <div className="row"><a className="btn" href="/api/export?format=xlsx">Download Excel</a><a className="btn soft" href="/api/export?format=csv">Download CSV</a></div>}
      </div>
      <div className="filters">
        <input type="search" placeholder="Search ticket, merchant or message" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} aria-label="Search" />
        {sel("action", plain(ACTIONS), "Any action")}
        {sel("category", plain(CATEGORIES), "Any category")}
        {sel("team", [...plain(TEAMS), ["none", "No escalation"]], "Any team")}
        {sel("risk", plain(RISKS), "Any risk")}
        {sel("validation", plain(["passed", "failed_fallback", "processing_failed"]), "Any check result")}
        {sel("lang", plain(["English", "Hinglish"]), "Any language")}
        {active > 0 && <button className="link" onClick={() => setF(EMPTY)}>Clear filters</button>}
      </div>
      {loading ? <p className="muted">Loading results.</p> : rows.length === 0 ? (
        <p className="muted">{given ? "Nothing here. Every ticket passed without needing a person." : "No results yet. Run the analysis from Overview."}</p>
      ) : (
        <div className="tablewrap">
          <table className="results">
            <thead><tr><th>Ticket</th><th>Merchant wrote</th><th>Category</th><th>Action</th><th>Team</th><th>Risk</th><th>Draft reply</th></tr></thead>
            <tbody>{shown.map((r) => (
              <tr key={r.ticket_id + (r.run_id || "")} onClick={() => setOpen(r)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(r)} className="click">
                <td><b>{r.ticket_id}</b><small className="muted block">{r.merchant_id || "No ID"}</small>
                  {r.human_review_required && <small className="flag">Needs a person</small>}</td>
                <td><div className="clip">{r.merchant_message}</div></td>
                <td>{r.category}</td>
                <td><Badge v={r.action} /></td>
                <td>{r.escalation_team || <span className="muted">None</span>}</td>
                <td><Badge v={r.risk_level} /></td>
                <td><div className="clip reply">{r.drafted_reply}</div></td>
              </tr>))}
            </tbody>
          </table>
        </div>
      )}
      {open && <TicketDetail r={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
