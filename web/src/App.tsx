import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { configured, sb } from "./lib";
import Overview from "./pages/Overview";
import Results from "./pages/Results";
import Review from "./pages/Review";
import Blocked from "./pages/Blocked";
import Evaluation from "./pages/Evaluation";
import Triage from "./pages/Triage";
import KbRules from "./pages/KbRules";
import Insights from "./pages/Insights";

type Tab = "overview" | "results" | "evaluation" | "review" | "blocked" | "triage" | "kb" | "insights";

const NAV: { group: string; items: { k: Tab; label: string; title: string; help: string }[] }[] = [
  { group: "Analyse", items: [
    { k: "overview", label: "Overview", title: "Overview", help: "Run the analysis on the 150 Data Pack tickets and see how they were triaged." },
    { k: "results", label: "All 150 results", title: "All 150 results", help: "The submission table: one row per ticket. Click a row to read the merchant message, the draft email and why the copilot decided it." },
    { k: "evaluation", label: "Evaluation", title: "Evaluation", help: "How the copilot's decisions compare with 46 hand-labelled tickets." },
  ] },
  { group: "Review", items: [
    { k: "review", label: "Needs a person", title: "Tickets that need a person", help: "Critical cases, blocked drafts and anything the copilot was unsure about. Nothing here is sent without a human." },
    { k: "blocked", label: "Blocked replies", title: "Replies that must not be sent", help: "Drafts the safety checker stopped, with the rule each one broke." },
    { k: "triage", label: "New tickets", title: "New tickets (Support Triage)", help: "Tickets arriving after the 150. The copilot suggests; a triage agent confirms or corrects before routing." },
  ] },
  { group: "Rules", items: [
    { k: "kb", label: "Knowledge base", title: "Knowledge base rules", help: "Versioned KB rules. Version 1 is the locked original; changes need a second person to approve." },
    { k: "insights", label: "Insights", title: "Insights", help: "Payment-failure spikes across merchants and gaps in the knowledge base." },
  ] },
];

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [roles, setRoles] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>(() => {
    try { return (localStorage.getItem("tab") as Tab) || "overview"; } catch { return "overview"; }
  });

  useEffect(() => {
    sb.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = sb.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);
  useEffect(() => {
    if (!session) { setRoles([]); return; }
    sb.rpc("my_roles").then(({ data }) => setRoles((data as string[]) || []));
  }, [session]);
  useEffect(() => { try { localStorage.setItem("tab", tab); } catch { /* ignore */ } }, [tab]);

  if (!configured) {
    return <div className="page"><div className="panel note-warn">The dashboard is missing its Supabase settings. Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in Vercel and redeploy.</div></div>;
  }
  const ctx = { session, roles, go: setTab };
  const current = NAV.flatMap((g) => g.items).find((i) => i.k === tab)!;

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <span className="mark" aria-hidden>
            <svg viewBox="0 0 24 24" width="18" height="18"><path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </span>
          <div><b>Support Copilot</b><small>Razorpay Build-a-thon, Problem 1</small></div>
        </div>
        <nav>
          {NAV.map((g) => (
            <div className="navgroup" key={g.group}>
              <span className="navhead">{g.group}</span>
              {g.items.map((i) => (
                <button key={i.k} className={tab === i.k ? "navitem on" : "navitem"} onClick={() => setTab(i.k)} aria-current={tab === i.k ? "page" : undefined}>
                  {i.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <Auth session={session} roles={roles} />
        <p className="sidefoot">Synthetic data. Auto-send is off: every reply is a draft for a person to send.</p>
      </aside>

      <main className="page">
        <header className="pagehead">
          <h1>{current.title}</h1>
          <p>{current.help}</p>
        </header>
        {tab === "overview" && <Overview {...ctx} />}
        {tab === "results" && <Results />}
        {tab === "evaluation" && <Evaluation />}
        {tab === "review" && <Review />}
        {tab === "blocked" && <Blocked {...ctx} />}
        {tab === "triage" && <Triage {...ctx} />}
        {tab === "kb" && <KbRules {...ctx} />}
        {tab === "insights" && <Insights />}
      </main>
    </div>
  );
}

const ROLE_NAMES: Record<string, string> = { operator: "Operator", triage_agent: "Triage agent", kb_editor: "KB editor", kb_approver: "KB approver" };

function Auth({ session, roles }: { session: Session | null; roles: string[] }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(false);
  if (session) {
    return (
      <div className="who">
        <span className="whoemail">{session.user.email}</span>
        <div className="chips">{roles.length ? roles.map((r) => <span key={r} className="chip">{ROLE_NAMES[r] || r}</span>) : <span className="muted">No role yet. Ask the owner for one.</span>}</div>
        <button className="link" onClick={() => sb.auth.signOut()}>Sign out</button>
      </div>
    );
  }
  if (!open) {
    return (
      <div className="who">
        <span className="muted">You are viewing read-only.</span>
        <button className="btn soft" onClick={() => setOpen(true)}>Sign in to run or review</button>
      </div>
    );
  }
  const go = async (signup: boolean) => {
    setMsg("");
    const r = signup ? await sb.auth.signUp({ email, password: pw }) : await sb.auth.signInWithPassword({ email, password: pw });
    if (r.error) setMsg(r.error.message);
    else if (signup) setMsg("Account created. Confirm your email if asked, then ask the owner for a role.");
  };
  return (
    <form className="who" onSubmit={(e) => { e.preventDefault(); go(false); }}>
      <input aria-label="Email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <input aria-label="Password" placeholder="Password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
      <div className="row">
        <button className="btn" type="submit">Sign in</button>
        <button className="link" type="button" onClick={() => go(true)}>Create account</button>
      </div>
      {msg && <span className="muted small">{msg}</span>}
    </form>
  );
}
