import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { ACTIONS, CATEGORIES, RISKS, TEAMS, fmtTime, sb } from "../lib";
import { Badge } from "../components";

export default function Triage({ session, roles }: { session: Session | null; roles: string[] }) {
  const [queue, setQueue] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [msg, setMsg] = useState("");
  const isTriage = roles.includes("triage_agent");
  const canSubmit = isTriage || roles.includes("operator");

  const load = async () => {
    const [q, a] = await Promise.all([
      sb.from("v_triage_queue").select("*"),
      sb.from("ai_alerts").select("*").order("raised_at", { ascending: false }).limit(100),
    ]);
    const rows = (q.data || []).sort((x: any, y: any) =>
      (x.review_status === "pending_triage" ? 0 : 1) - (y.review_status === "pending_triage" ? 0 : 1) ||
      x.risk_rank - y.risk_rank || String(x.processed_at).localeCompare(String(y.processed_at)));
    setQueue(rows); setAlerts(a.data || []);
  };
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);

  const ack = async (id: number) => {
    const { error } = await sb.rpc("ack_alert", { p_alert_id: id });
    setMsg(error ? error.message : "Alert acknowledged."); load();
  };

  return (
    <>
      <section className="card">
        <h2>Critical alerts</h2>
        <p className="muted">Raised the moment a new ticket hits the critical-risk gate (KB-15). They do not wait for triage.</p>
        {alerts.length === 0 && <p>No alerts.</p>}
        {alerts.map((a) => (
          <div key={a.alert_id} className={`alert ${a.acknowledged_at ? "acked" : ""}`}>
            <b>{a.team}</b> | {a.message} <small className="muted">raised {fmtTime(a.raised_at)}</small>
            {a.acknowledged_at ? <small className="muted"> | acknowledged {fmtTime(a.acknowledged_at)}</small>
              : isTriage && <button className="ghost" onClick={() => ack(a.alert_id)}>Acknowledge</button>}
          </div>))}
      </section>

      {canSubmit && <NewTicket onDone={(m) => { setMsg(m); setTimeout(load, 4000); }} />}
      {msg && <p className="muted">{msg}</p>}

      <section className="card">
        <h2>Support Triage queue (new tickets)</h2>
        <p className="muted">The AI prepares each new ticket; a triage agent confirms or corrects it before it is routed. Critical first, then High, then oldest.
          {!isTriage && " Sign in as a triage agent to act."}</p>
        {queue.length === 0 && <p>No new tickets yet. Submit a test ticket above to see review mode.</p>}
        {queue.map((r) => <QueueItem key={r.analysis_id} r={r} canAct={isTriage} onDone={(m) => { setMsg(m); load(); }} />)}
      </section>
    </>
  );
}

function NewTicket({ onDone }: { onDone: (m: string) => void }) {
  const [mid, setMid] = useState("M1015");
  const [ch, setCh] = useState("Chat");
  const [text, setText] = useState("");
  const submit = async () => {
    const { data, error } = await sb.rpc("submit_ticket", { p_merchant_id: mid, p_channel: ch, p_message: text });
    if (error) onDone(error.message);
    else { setText(""); onDone(`Ticket ${data} created. The webhook processes it in a few seconds.`); }
  };
  return (
    <section className="card">
      <h2>Simulate a new incoming ticket</h2>
      <p className="muted">Appends a ticket through the insert-only intake role. The 150 original tickets are never changed.</p>
      <div className="row wrap">
        <input value={mid} onChange={(e) => setMid(e.target.value)} placeholder="Merchant ID (optional)" style={{ width: 140 }} />
        <select value={ch} onChange={(e) => setCh(e.target.value)}><option>Chat</option><option>Email</option><option>WhatsApp</option></select>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Merchant message" style={{ flex: 1, minWidth: 260 }} />
        <button onClick={submit} disabled={!text.trim()}>Submit</button>
      </div>
    </section>
  );
}

function QueueItem({ r, canAct, onDone }: { r: any; canAct: boolean; onDone: (m: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [c, setC] = useState({ category: r.category, action: r.action, escalation_team: r.escalation_team || "", risk_level: r.risk_level, drafted_reply: r.drafted_reply });
  const [reason, setReason] = useState("");
  const pending = r.review_status === "pending_triage";

  const decide = async (decision: "confirmed" | "corrected") => {
    const corrections: Record<string, string> = {};
    if (decision === "corrected") {
      (Object.keys(c) as (keyof typeof c)[]).forEach((k) => { if ((c[k] || "") !== (r[k] || "")) corrections[k] = c[k]; });
    }
    const { error } = await sb.rpc("triage_decide", { p_analysis_id: r.analysis_id, p_decision: decision, p_corrections: corrections, p_reason: reason || null });
    onDone(error ? error.message : `${r.ticket_id} ${decision} and routed.`);
  };

  return (
    <div className={`qitem ${r.risk_level === "Critical" ? "crit" : ""}`}>
      <div className="row between">
        <div><b>{r.ticket_id}</b> <small className="muted">{r.ticket_created_at} | {r.channel} | {r.merchant_id || "no merchant ID"}</small></div>
        <div>{pending ? <span className="badge b-ask">Waiting for triage</span> : <span className="badge b-low">Routed ({r.triage_decision})</span>}</div>
      </div>
      <div className="quote">{r.merchant_message}</div>
      <div className="grid4">
        <div><label>AI category</label>{r.category}</div><div><label>AI action</label><Badge v={r.action} /></div>
        <div><label>AI team</label>{r.escalation_team || "-"}</div><div><label>AI risk</label><Badge v={r.risk_level} /></div>
      </div>
      {!pending && r.triage_decision === "corrected" && (
        <p className="muted">Final: {r.final_category} / {r.final_action} / {r.final_escalation_team || "-"} / {r.final_risk_level}. Reason: {r.triage_reason}</p>)}
      <details><summary>AI draft reply ({r.validation_status})</summary><pre>{r.drafted_reply}</pre></details>
      {pending && canAct && !edit && (
        <div className="row"><button onClick={() => decide("confirmed")}>Confirm and route</button><button className="ghost" onClick={() => setEdit(true)}>Correct</button></div>)}
      {pending && canAct && edit && (
        <div className="editbox">
          <div className="row wrap">
            <select value={c.category} onChange={(e) => setC({ ...c, category: e.target.value })}>{CATEGORIES.map((x) => <option key={x}>{x}</option>)}</select>
            <select value={c.action} onChange={(e) => setC({ ...c, action: e.target.value })}>{ACTIONS.map((x) => <option key={x}>{x}</option>)}</select>
            <select value={c.escalation_team} onChange={(e) => setC({ ...c, escalation_team: e.target.value })}><option value="">(no team)</option>{TEAMS.map((x) => <option key={x}>{x}</option>)}</select>
            <select value={c.risk_level} onChange={(e) => setC({ ...c, risk_level: e.target.value })}>{RISKS.map((x) => <option key={x}>{x}</option>)}</select>
          </div>
          <textarea rows={6} value={c.drafted_reply} onChange={(e) => setC({ ...c, drafted_reply: e.target.value })} />
          <input placeholder="Reason for the correction (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="row"><button onClick={() => decide("corrected")} disabled={!reason.trim()}>Save correction and route</button><button className="ghost" onClick={() => setEdit(false)}>Cancel</button></div>
        </div>)}
    </div>
  );
}
