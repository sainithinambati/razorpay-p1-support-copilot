import { useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { api, fetchAll, fmtTime, sb } from "../lib";
import { Bars, countBy } from "../components";

const PARALLEL_WORKERS = 3;
const SPLIT = [
  { k: "AUTO_RESOLVE", label: "Auto-resolve", tone: "t-auto", hint: "Safe to answer from the KB and merchant record" },
  { k: "ASK_FOR_INFO", label: "Ask for info", tone: "t-ask", hint: "Merchant ID, payment ID or detail missing" },
  { k: "ESCALATE", label: "Escalate", tone: "t-esc", hint: "Needs a specialist team" },
];
const RISK_TONES = { Critical: "t-crit", High: "t-high", Medium: "t-med", Low: "t-low" };

export default function Overview({ session, roles, go }: { session: Session | null; roles: string[]; go: (t: any) => void }) {
  const [mode, setMode] = useState("all");
  const [runs, setRuns] = useState<any[]>([]);
  const [rows, setRows] = useState<any[]>([]);
  const [integrity, setIntegrity] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [active, setActive] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [starting, setStarting] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const stop = useRef(false);

  const load = async () => {
    const [r, c, i] = await Promise.all([
      sb.from("v_run_progress").select("*").order("created_at", { ascending: false }).limit(30),
      fetchAll("v_current_results", "ticket_id,category,action,escalation_team,risk_level,validation_status,human_review_required"),
      sb.rpc("source_integrity"),
    ]);
    setRuns(r.data || []); setRows(c); setIntegrity((i.data as any[]) || []);
  };
  useEffect(() => { load(); api("/api/health", null).then(setHealth).catch(() => setHealth(null)); }, []);

  const drive = async (runId: string) => {
    stop.current = false;
    setMsg("Working through the tickets. Keep this tab open until it finishes.");
    const worker = async () => {
      while (!stop.current) {
        try {
          const res = await api("/api/worker", session, { run_id: runId, limit: 2 });
          if (res.progress) setActive(res.progress);
          const p = res.progress;
          if (!p || (p.pending === 0 && p.processing === 0)) return;
          if (!res.processed.length) await new Promise((r) => setTimeout(r, 2500));
        } catch (e: any) {
          setMsg(`A worker hit an error (${e.message}). It will retry in 5 seconds.`);
          await new Promise((r) => setTimeout(r, 5000));
        }
      }
    };
    await Promise.all(Array.from({ length: PARALLEL_WORKERS }, worker));
    setMsg(stop.current ? "Paused. Choose Resume to continue this run." : "Analysis finished. The results below are up to date.");
    await load();
  };

  const start = async () => {
    if (starting) return;
    setStarting(true); setMsg("");
    const { data, error } = await sb.rpc("start_run", { p_mode: mode });
    setStarting(false);
    if (error) { setMsg(error.message); return; }
    setActive({ run_id: data, total_jobs: 0, done: 0, failed: 0, pending: 1, processing: 0 });
    drive(data as string);
  };

  const canRun = true; // public demo: anyone can run; the database limits how many runs exist
  const running = active && active.pending + active.processing > 0;
  const unfinished = runs.find((r) => r.run_type === "batch" && (r.pending > 0 || r.processing > 0));
  const n = rows.length;
  const byAction = countBy(rows, (r: any) => r.action);
  const review = rows.filter((r) => r.human_review_required).length;
  const blocked = rows.filter((r) => r.validation_status === "failed_fallback").length;
  const latest = runs.find((r) => r.run_type === "batch");
  const allOk = integrity.length === 3 && integrity.every((r) => r.original_rows_unchanged);
  const pct = active?.total_jobs ? Math.round((100 * (active.done + active.failed)) / active.total_jobs) : 0;

  return (
    <>
      <section className="panel split">
        <div className="row between wrap">
          <h2>{n ? `How the ${n} tickets were triaged` : "No results yet"}</h2>
          {latest && <span className="muted small">Latest run {fmtTime(latest.created_at)}, wording by {latest.model_name === "deterministic-template" ? "approved templates" : latest.model_name || "pending"}</span>}
        </div>
        {n > 0 ? (
          <>
            <div className="splitbar" role="img" aria-label={SPLIT.map((s) => `${s.label} ${byAction[s.k] || 0}`).join(", ")}>
              {SPLIT.map((s) => (byAction[s.k] ? <span key={s.k} className={s.tone} style={{ flexGrow: byAction[s.k] }} /> : null))}
            </div>
            <div className="splitlegend">
              {SPLIT.map((s) => (
                <div key={s.k}>
                  <span className={`dot ${s.tone}`} />
                  <b>{byAction[s.k] || 0}</b> {s.label}
                  <small>{s.hint}</small>
                </div>
              ))}
            </div>
            <div className="callouts">
              <button className="callout" onClick={() => go("review")}><b>{review}</b> need a person before anything is sent</button>
              <button className="callout" onClick={() => go("blocked")}><b>{blocked}</b> drafts blocked by the safety checker</button>
              <span className={`callout static ${allOk ? "good" : "bad"}`}>{allOk ? "Original Razorpay data unchanged" : "Original data check failed"}</span>
            </div>
          </>
        ) : <p className="muted">Run the analysis to see how the 150 tickets split between auto-resolve, ask for info and escalate.</p>}
      </section>

      <section className="panel">
        <h2>Run the analysis</h2>
        <ol className="steps">
          <li className={n ? "done" : "now"}><b>Run</b><span>Analyse all 150 tickets. Each run is kept; nothing is overwritten.</span></li>
          <li className={n ? "now" : ""}><b>Check</b><span>Look at the tickets that need a person and any blocked replies.</span></li>
          <li><b>Export</b><span>Download the 6-column table from All 150 results.</span></li>
        </ol>
        <div className="row wrap">
          <select value={mode} onChange={(e) => setMode(e.target.value)} disabled={running} aria-label="Which tickets to run">
            <option value="all">All 150 tickets</option>
            <option value="remaining">Only tickets never analysed</option>
            <option value="failed">Only tickets that failed</option>
          </select>
          <button className="btn" onClick={start} disabled={starting || !canRun || running}>{running ? "Running" : "Run analysis"}</button>
          {running && <button className="link" onClick={() => (stop.current = true)}>Pause</button>}
          {unfinished && !active && canRun && <button className="btn soft" onClick={() => { setActive(unfinished); drive(unfinished.run_id); }}>Resume unfinished run</button>}
        </div>
        {active && (
          <div className="progress">
            <div className="pbar"><span style={{ width: `${pct}%` }} /></div>
            <span className="small">{active.done + active.failed} of {active.total_jobs || 150} tickets done{active.failed ? `, ${active.failed} failed` : ""}</span>
          </div>
        )}
        {msg && <p className="muted small">{msg}</p>}
      </section>

      <div className="grid2">
        <Bars title="Escalations by team" data={countBy(rows.filter((r: any) => r.escalation_team), (r: any) => r.escalation_team)} tones={{}} />
        <Bars title="Risk level" data={countBy(rows, (r: any) => r.risk_level)} tones={RISK_TONES} />
      </div>
      <Bars title="Categories" data={countBy(rows, (r: any) => r.category)} />

      <section className="panel">
        <div className="row between wrap">
          <h2>System status</h2>
          <button className="link" onClick={() => setShowHistory(!showHistory)}>{showHistory ? "Hide" : "Show"} run history ({runs.length})</button>
        </div>
        <ul className="checks">
          <li className={allOk ? "ok" : "bad"}>Original data: {integrity.map((r) => `${r.current_rows} ${r.table_name.replace("p1_", "")}`).join(", ")}{allOk ? ", all unchanged" : ", changed"}</li>
          {health ? (
            <>
              <li className={health.database?.connected ? "ok" : "bad"}>Database: {health.database?.connected ? `connected with the restricted ${health.database.role} login` : "not connected"}</li>
              <li className={health.claude_configured ? "ok" : "bad"}>Claude: {health.claude_configured ? `ready (${health.claude_model})` : "not set up, so keyword rules and approved templates are used"}</li>
              <li className={health.webhook_secret_set && health.worker_secret_set ? "ok" : "warn"}>New-ticket automation: {health.webhook_secret_set && health.worker_secret_set ? "on" : "off until the webhook and worker secrets are added"}</li>
              <li className="ok">Sending: {health.auto_send_enabled ? "ON" : "off, every reply is a draft"}</li>
            </>
          ) : <li className="warn">The API is not responding.</li>}
        </ul>
        {showHistory && (
          <div className="tablewrap">
            <table>
              <thead><tr><th>Started</th><th>Type</th><th>Status</th><th>Rules</th><th>Wording</th><th>Done</th><th>Failed</th></tr></thead>
              <tbody>{runs.map((r) => (
                <tr key={r.run_id}><td>{fmtTime(r.created_at)}</td><td>{r.run_type === "batch" ? "150 tickets" : "New ticket"}</td><td>{r.status}</td>
                  <td>{r.rules_version}</td><td>{r.model_name === "deterministic-template" ? "Templates" : r.model_name || "-"}</td><td>{r.done}/{r.total_jobs}</td><td>{r.failed}</td></tr>))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
