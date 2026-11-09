import { useState } from "react";

export const Badge = ({ v }: { v?: string | null }) => {
  if (!v) return <span className="muted">-</span>;
  const cls = ({ Critical: "b-crit", High: "b-high", Medium: "b-med", Low: "b-low", ESCALATE: "b-esc", ASK_FOR_INFO: "b-ask",
    AUTO_RESOLVE: "b-auto", passed: "b-low", failed_fallback: "b-high", processing_failed: "b-crit" } as Record<string, string>)[v] || "b-plain";
  return <span className={`badge ${cls}`}>{v}</span>;
};

export function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return <div className="stat"><div className="v">{value}</div><div className="l">{label}</div>{sub && <div className="s">{sub}</div>}</div>;
}

export function Bars({ title, data }: { title: string; data: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(data));
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return (
    <div className="card">
      <h3>{title}</h3>
      {entries.length === 0 && <p className="muted">No data yet.</p>}
      {entries.map(([k, n]) => (
        <div className="bar" key={k}>
          <span className="bl">{k || "(none)"}</span>
          <span className="bt"><span style={{ width: `${(100 * n) / max}%` }} /></span>
          <span className="bn">{n}</span>
        </div>
      ))}
    </div>
  );
}

export function countBy<T>(rows: T[], f: (r: T) => string | null | undefined) {
  const m: Record<string, number> = {};
  rows.forEach((r) => { const k = f(r) || "(none)"; m[k] = (m[k] || 0) + 1; });
  return m;
}

export function TicketDetail({ r, onClose }: { r: any; onClose: () => void }) {
  const [showTrace, setShowTrace] = useState(false);
  const subject = `Re: Ticket ${r.ticket_id}${r.category ? ` (${r.category})` : ""}`;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="row between"><h2>{r.ticket_id}</h2><button className="ghost" onClick={onClose}>Close</button></div>
        <p className="muted">{r.ticket_created_at} | {r.channel} | merchant {r.merchant_id || "missing"} | {r.language_style} | tone {r.tone}</p>
        <div className="quote">{r.merchant_message}</div>
        <div className="grid4">
          <div><label>Category</label><b>{r.category}</b>{r.secondary_topic && <small> (topic: {r.secondary_topic})</small>}</div>
          <div><label>Action</label><Badge v={r.action} /></div>
          <div><label>Team</label><b>{r.escalation_team || "-"}</b></div>
          <div><label>Risk</label><Badge v={r.risk_level} /></div>
        </div>
        <h3>Draft email (not sent)</h3>
        <div className="email">
          <div><label>To</label>{r.merchant_id ? `Merchant ${r.merchant_id}` : "Merchant (ID not provided)"}</div>
          <div><label>Subject</label>{subject}</div>
          <pre>{r.drafted_reply}</pre>
          <button className="ghost" onClick={() => navigator.clipboard?.writeText(r.drafted_reply)}>Copy body</button>
        </div>
        <div className="grid4">
          <div><label>Validation</label><Badge v={r.validation_status} /></div>
          <div><label>Human review</label><b>{r.human_review_required ? "Required" : "No"}</b></div>
          <div><label>KB used</label><b>{(r.relevant_kb_ids || []).join(", ")}</b></div>
          <div><label>Rules / model</label><small>{r.rules_version} / {r.model_name || "-"} ({r.classifier_source})</small></div>
        </div>
        {r.failure_reason && <p className="warnline">{r.failure_reason}</p>}
        <h3>Merchant facts used</h3>
        <pre className="small">{JSON.stringify(r.merchant_facts_used || {}, null, 2)}</pre>
        <button className="ghost" onClick={() => setShowTrace(!showTrace)}>{showTrace ? "Hide" : "Show"} decision trace</button>
        {showTrace && <ol className="trace">{(r.decision_trace || []).map((t: string, i: number) => <li key={i}>{t}</li>)}</ol>}
      </div>
    </div>
  );
}
