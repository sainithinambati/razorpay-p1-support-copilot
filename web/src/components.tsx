import { useEffect, useState } from "react";

export const LABELS: Record<string, string> = {
  AUTO_RESOLVE: "Auto-resolve", ASK_FOR_INFO: "Ask for info", ESCALATE: "Escalate",
  passed: "Checks passed", failed_fallback: "Blocked, safe fallback used", processing_failed: "Processing failed",
  Critical: "Critical", High: "High", Medium: "Medium", Low: "Low",
};
const TONE: Record<string, string> = {
  AUTO_RESOLVE: "t-auto", ASK_FOR_INFO: "t-ask", ESCALATE: "t-esc",
  Critical: "t-crit", High: "t-high", Medium: "t-med", Low: "t-low",
  passed: "t-auto", failed_fallback: "t-high", processing_failed: "t-crit",
};

export const Badge = ({ v }: { v?: string | null }) => {
  if (!v) return <span className="muted">None</span>;
  return <span className={`pill ${TONE[v] || "t-plain"}`}>{LABELS[v] || v}</span>;
};

export function Stat({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: string }) {
  return <div className={`stat ${tone || ""}`}><div className="v">{value}</div><div className="l">{label}</div>{sub && <div className="s">{sub}</div>}</div>;
}

export function Bars({ title, data, tones }: { title: string; data: Record<string, number>; tones?: Record<string, string> }) {
  const max = Math.max(1, ...Object.values(data));
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return (
    <div className="panel">
      <h3>{title}</h3>
      {entries.length === 0 && <p className="muted">Appears after the first run.</p>}
      {entries.map(([k, n]) => (
        <div className="bar" key={k}>
          <span className="bl">{LABELS[k] || k}</span>
          <span className="bt"><span className={tones?.[k] || "t-fill"} style={{ width: `${(100 * n) / max}%` }} /></span>
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
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const subject = `Re: Ticket ${r.ticket_id}${r.category ? ` (${r.category})` : ""}`;
  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" role="dialog" aria-label={`Ticket ${r.ticket_id}`} onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h2>{r.ticket_id}</h2>
          <button className="link" onClick={onClose}>Close</button>
        </div>
        <p className="muted small">{r.ticket_created_at}, {r.channel}, merchant {r.merchant_id || "not given"}, written in {r.language_style}{r.tone && r.tone !== "neutral" ? `, merchant sounds ${r.tone}` : ""}</p>

        <h3>What the merchant wrote</h3>
        <blockquote>{r.merchant_message}</blockquote>

        <h3>What the copilot decided</h3>
        <div className="facts">
          <div><span>Category</span><b>{r.category}</b>{r.secondary_topic && <small>about {r.secondary_topic}</small>}</div>
          <div><span>Action</span><Badge v={r.action} /></div>
          <div><span>Team</span><b>{r.escalation_team || "No escalation"}</b></div>
          <div><span>Risk</span><Badge v={r.risk_level} /></div>
        </div>
        {r.human_review_required && <p className="note-warn">A person must review this before anything is sent{r.failure_reason ? `: ${r.failure_reason}` : "."}</p>}

        <h3>Draft email (not sent)</h3>
        <div className="email">
          <div className="emailhead"><span>To</span>{r.merchant_id ? `Merchant ${r.merchant_id}` : "Merchant (ID not provided)"}</div>
          <div className="emailhead"><span>Subject</span>{subject}</div>
          <pre>{r.drafted_reply}</pre>
          <button className="btn soft" onClick={() => { navigator.clipboard?.writeText(r.drafted_reply); setCopied(true); }}>{copied ? "Copied" : "Copy email body"}</button>
        </div>

        <h3>Why</h3>
        <div className="facts">
          <div><span>Safety checks</span><Badge v={r.validation_status} /></div>
          <div><span>KB articles</span><b>{(r.relevant_kb_ids || []).join(", ")}</b></div>
          <div><span>Rules version</span><b>{r.rules_version}</b></div>
          <div><span>Wording by</span><b>{r.model_name === "deterministic-template" ? "Approved template" : r.model_name || "None"}</b></div>
        </div>
        {Object.keys(r.merchant_facts_used || {}).length > 0 && (
          <>
            <h3>Merchant facts used</h3>
            <dl className="kv">{Object.entries(r.merchant_facts_used).map(([k, v]) => <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></div>)}</dl>
          </>
        )}
        <button className="link" onClick={() => setShowTrace(!showTrace)}>{showTrace ? "Hide" : "Show"} step-by-step decision trace</button>
        {showTrace && <ol className="trace">{(r.decision_trace || []).map((t: string, i: number) => <li key={i}>{t}</li>)}</ol>}
      </div>
    </div>
  );
}
