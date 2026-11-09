import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { TEAMS, fmtTime, sb } from "../lib";

export default function KbRules({ roles }: { session: Session | null; roles: string[] }) {
  const [versions, setVersions] = useState<any[]>([]);
  const [view, setView] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [draft, setDraft] = useState<any>(null);
  const editor = roles.includes("kb_editor");
  const approver = roles.includes("kb_approver");

  const load = async () => {
    const { data } = await sb.from("kb_rule_versions").select("*").order("version_id", { ascending: false });
    setVersions(data || []);
    setView((v: any) => (data || []).find((x: any) => x.version_id === v?.version_id) || (data || []).find((x: any) => x.status === "active"));
  };
  useEffect(() => { load(); }, []);

  const act = async (fn: string, id: number) => {
    const { error } = await sb.rpc(fn, { p_version_id: id });
    setMsg(error ? error.message : "Done."); load();
  };
  const startDraft = () => {
    const a = versions.find((x) => x.status === "active");
    setDraft({ based_on: a.version_id, articles: structuredClone(a.articles), parameters: JSON.stringify(a.parameters, null, 2),
      team_mapping: { ...a.team_mapping }, kb: "KB-01", reason: "" });
  };
  const saveDraft = async () => {
    let params;
    try { params = JSON.parse(draft.parameters); } catch { setMsg("Parameters must be valid JSON."); return; }
    const { data, error } = await sb.rpc("kb_create_draft", { p_based_on: draft.based_on, p_articles: draft.articles,
      p_parameters: params, p_team_mapping: draft.team_mapping, p_reason: draft.reason });
    setMsg(error ? error.message : `Draft kb-v${data} created. A different person with the approver role must approve it.`);
    if (!error) { setDraft(null); load(); }
  };

  return (
    <>
      <section className="card">
        <h2>KB rule versions</h2>
        <p className="muted">Version 1 is an exact, locked copy of the original Data Pack KB and is always used for the 150-ticket submission runs.
          The Support Triage team can propose new versions for new tickets; a second person must approve. Safety rules (critical-risk gate, no promises,
          no auto-send, output validation) are not parameters and cannot be changed here. The AI can only suggest changes (see Insights).</p>
        {msg && <p className="warnline">{msg}</p>}
        <table><thead><tr><th>Version</th><th>Status</th><th>Changed</th><th>Reason</th><th>Created</th><th>Approved</th><th></th></tr></thead>
          <tbody>{versions.map((v) => (
            <tr key={v.version_id} className={view?.version_id === v.version_id ? "selrow" : ""}>
              <td><b>{v.rules_version}</b>{v.is_baseline && <small> (baseline, locked)</small>}</td><td>{v.status}</td>
              <td><small>{(v.changed_kb_ids || []).join(", ") || "-"}</small></td><td><small>{v.change_reason}</small></td>
              <td><small>{fmtTime(v.created_at)}</small></td><td><small>{fmtTime(v.approved_at)}</small></td>
              <td className="row">
                <button className="ghost" onClick={() => setView(v)}>View</button>
                {approver && v.status === "draft" && <><button onClick={() => act("kb_approve", v.version_id)}>Approve</button><button className="ghost" onClick={() => act("kb_reject", v.version_id)}>Reject</button></>}
                {approver && (v.status === "approved" || v.status === "retired") && <button onClick={() => act("kb_activate", v.version_id)}>Activate</button>}
              </td>
            </tr>))}
          </tbody></table>
        {editor && !draft && <button onClick={startDraft}>Propose a change</button>}
      </section>

      {draft && (
        <section className="card">
          <h2>New draft (based on kb-v{draft.based_on})</h2>
          <div className="cols2">
            <div>
              <h3>Article text</h3>
              <select value={draft.kb} onChange={(e) => setDraft({ ...draft, kb: e.target.value })}>
                {Object.keys(draft.articles).sort().map((k) => <option key={k}>{k}</option>)}
              </select>
              <textarea rows={6} value={draft.articles[draft.kb].guidance}
                onChange={(e) => setDraft({ ...draft, articles: { ...draft.articles, [draft.kb]: { ...draft.articles[draft.kb], guidance: e.target.value } } })} />
              <h3>Thresholds and timelines</h3>
              <textarea rows={14} className="mono" value={draft.parameters} onChange={(e) => setDraft({ ...draft, parameters: e.target.value })} />
            </div>
            <div>
              <h3>Routing</h3>
              {Object.keys(draft.team_mapping).sort().map((k) => (
                <div className="row" key={k}><span style={{ width: 170 }}>{k}</span>
                  <select value={draft.team_mapping[k]} onChange={(e) => setDraft({ ...draft, team_mapping: { ...draft.team_mapping, [k]: e.target.value } })}>
                    {TEAMS.map((t) => <option key={t}>{t}</option>)}</select></div>))}
              <h3>Reason for the change</h3>
              <input value={draft.reason} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} placeholder="Why is this change needed?" />
              <div className="row"><button onClick={saveDraft}>Save draft</button><button className="ghost" onClick={() => setDraft(null)}>Cancel</button></div>
            </div>
          </div>
        </section>)}

      {view && (
        <section className="card">
          <h2>{view.rules_version} ({view.status})</h2>
          <div className="cols2">
            <div>{Object.keys(view.articles).sort().map((k) => (
              <p key={k}><b>{k} {view.articles[k].topic}</b><br /><small>{view.articles[k].guidance}</small></p>))}</div>
            <div><h3>Parameters</h3><pre className="small">{JSON.stringify(view.parameters, null, 2)}</pre>
              <h3>Routing</h3><pre className="small">{JSON.stringify(view.team_mapping, null, 2)}</pre></div>
          </div>
        </section>)}
    </>
  );
}
