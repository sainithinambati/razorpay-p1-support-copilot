import { useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { api, fetchAll, fmtTime, pct, sb } from "../lib";
import { Bars, Stat, countBy } from "../components";

const PARALLEL_WORKERS = 3;

export default function Overview({ session, roles }: { session: Session | null; roles: string[] }) {
  const [mode, setMode] = useState("all");
  const [runs, setRuns] = useState<any[]>([]);
  const [current, setCurrent] = useState<any[]>([]);
  const [integrity, setIntegrity] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [active, setActive] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const stop = useRef(false);

  const load = async () => {
    const [r, c, i] = await Promise.all([
      sb.from("v_run_progress").select("*").order("created_at", { ascending: false }).limit(30),
      fetchAll("v_current_results", "ticket_id,category,action,escalation_team,risk_level,validation_status,human_review_required,language_style"),
      sb.rpc("source_integrity"),
    ]);
    setRuns(r.data || []); setCurrent(c); setIntegrity((i.data as any[]) || []);
  };
  useEffect(() => { load(); api("/api/health", null).then(setHealth).catch(() => setHealth(null)); }, []);

  const drive = async (runId: string) => {
    stop.current = false;
    setMsg("Processing. Keep this tab open; if you close it, the background sweeper continues more slowly.");
    const worker = async () => {
      while (!stop.current) {
        try {
          const res = await api("/api/worker", session, { run_id: runId, limit: 2 });
          if (res.progress) setActive(res.progress);
          const p = res.progress;
          if (!p || (p.pending === 0 && p.processing === 0)) return;
          if (!res.processed.length) await new Promise((r) => setTimeout(r, 2500));
        } catch (e: any) {
          setMsg(`Worker error: ${e.message}. Retrying in 5 seconds.`);
          await new Promise((r) => setTimeout(r, 5000));
        }
      }
    };
    await Promise.all(Array.from({ length: PARALLEL_WORKERS }, worker));
    setMsg(stop.current ? "Paused. Use Resume to continue this run." : "Run finished.");
    await load();
  };

  const [starting, setStarting] = useState(false);
  const start = async () => {
    if (starting) return;
    setStarting(true);
    setMsg("");
    const { data, error } = await sb.rpc("start_run", { p_mode: mode });
    setStarting(false);
    if (error) { setMsg(error.message); return; }
    setActive({ run_id: data, total_jobs: 0, done: 0, failed: 0, pending: 0, processing: 0 });
    drive(data as string);
  };

  const isOperator = roles.includes("operator");
  const batch = current;
  const auto = batch.filter((r) => r.action === "AUTO_RESOLVE").length;
  const unfinished = runs.find((r) => r.run_type === "batch" && (r.pending > 0 || r.processing > 0));

  return (
    <>
      <section className="card">
        <h2>Run AI Analysis</h2>
        <p className="muted">Processes the 150 Data Pack tickets with the baseline KB (kb-v1). Every run gets a new run ID; earlier results are kept.</p>
        <div className="row">
          <select value={mode} onChange={(e) => setMode(e.target.value)} disabled={!isOperator}>
            <option value="all">Run all tickets (150)</option>
            <option value="remaining">Run remaining tickets (never analysed)</option>
            <option value="failed">Rerun failed tickets</option>
          </select>
          <button onClick={start} disabled={starting || !isOperator || (active && active.pending + active.processing > 0)}>Run AI Analysis</button>
          {active && active.pending + active.processing > 0 && <button className="ghost" onClick={() => (stop.current = true)}>Pause</button>}
          {unfinished && !active && isOperator && <button className="ghost" onClick={() => { setActive(unfinished); drive(unfinished.run_id); }}>Resume unfinished run</button>}
          {!isOperator && <span className="muted">Sign in with an operator account to run.</span>}
        </div>
        {active && (
          <div className="progress">
            <div className="pbar"><span style={{ width: `${active.total_jobs ? (100 * (active.done + active.failed)) / active.total_jobs : 0}%` }} /></div>
            <small>{active.done + active.failed} of {active.total_jobs || "?"} processed | {active.failed} failed | {active.processing} in progress</small>
          </div>
        )}
        {msg && <p className="muted">{msg}</p>}
      </section>

      <section className="stats">
        <Stat label="Tickets with a current result" value={`${batch.length} / 150`} />
        <Stat label="Auto-resolve share" value={pct(auto, batch.length)} sub={`${auto} tickets eligible (drafts only)`} />
        <Stat label="Escalated" value={batch.filter((r) => r.action === "ESCALATE").length} />
        <Stat label="Ask for info" value={batch.filter((r) => r.action === "ASK_FOR_INFO").length} />
        <Stat label="Need human review" value={batch.filter((r) => r.human_review_required).length} />
        <Stat label="Drafts blocked by validation" value={batch.filter((r) => r.validation_status === "failed_fallback").length} />
      </section>

      <div className="cols">
        <Bars title="Actions" data={countBy(batch, (r: any) => r.action)} />
        <Bars title="Escalation teams" data={countBy(batch.filter((r: any) => r.escalation_team), (r: any) => r.escalation_team)} />
        <Bars title="Risk levels" data={countBy(batch, (r: any) => r.risk_level)} />
        <Bars title="Categories" data={countBy(batch, (r: any) => r.category)} />
      </div>

      <section className="card">
        <h2>Source-data integrity</h2>
        <p className="muted">Fingerprint of the original Razorpay data, compared with the copy taken when it was loaded. The database blocks every update and delete on these tables.</p>
        <table><thead><tr><th>Table</th><th>Original rows</th><th>Rows now</th><th>Original rows unchanged</th><th>New tickets appended</th></tr></thead>
          <tbody>{integrity.map((r) => (
            <tr key={r.table_name}><td>{r.table_name}</td><td>{r.baseline_rows}</td><td>{r.current_rows}</td>
              <td>{r.original_rows_unchanged ? <span className="ok">Yes</span> : <span className="bad">NO</span>}</td><td>{r.appended_rows}</td></tr>))}
          </tbody></table>
      </section>

      <section className="card">
        <h2>Run history</h2>
        <table><thead><tr><th>Started</th><th>Type</th><th>Trigger</th><th>Status</th><th>Rules</th><th>Model</th><th>Done</th><th>Failed</th><th>Pending</th></tr></thead>
          <tbody>{runs.map((r) => (
            <tr key={r.run_id}><td>{fmtTime(r.created_at)}</td><td>{r.run_type}</td><td>{r.trigger_type}</td><td>{r.status}</td>
              <td>{r.rules_version}</td><td>{r.model_name || "-"}</td><td>{r.done}/{r.total_jobs}</td><td>{r.failed}</td><td>{r.pending + r.processing}</td></tr>))}
          </tbody></table>
      </section>

      <section className="card">
        <h2>System status</h2>
        {!health && <p className="muted">API not reachable.</p>}
        {health && (
          <ul className="checks">
            <li className={health.database?.connected ? "ok" : "bad"}>Database: {health.database?.connected ? `connected as ${health.database.role}` : "not connected (set DATABASE_URL)"}</li>
            <li className={health.claude_configured ? "ok" : "bad"}>Claude: {health.claude_configured ? `configured (${health.claude_model})` : "not configured: keyword classifier and approved templates are used"}</li>
            <li className={health.worker_secret_set ? "ok" : "bad"}>Sweeper secret: {health.worker_secret_set ? "set" : "missing"}</li>
            <li className={health.webhook_secret_set ? "ok" : "bad"}>Webhook secret: {health.webhook_secret_set ? "set" : "missing"}</li>
            <li className="ok">Auto-send: {health.auto_send_enabled ? "ON" : "disabled (drafts only)"}</li>
          </ul>
        )}
      </section>
    </>
  );
}
