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

const TABS = [
  ["overview", "Run & status"], ["results", "150-ticket results"], ["review", "Human review"], ["blocked", "Blocked replies"],
  ["evaluation", "Evaluation"], ["triage", "Triage queue"], ["kb", "KB rules"], ["insights", "Insights"],
] as const;
type Tab = typeof TABS[number][0];

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
    return <div className="wrap"><div className="card warn">Supabase URL and anon key are not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in Vercel and redeploy.</div></div>;
  }
  const ctx = { session, roles };
  return (
    <>
      <header className="top">
        <div className="brand">
          <span className="logo">P1</span>
          <div><b>Merchant Support Copilot</b><small>Razorpay Campus AI Build-a-thon, Problem 1. Synthetic data. Auto-send is off: every reply is a draft.</small></div>
        </div>
        <Auth session={session} roles={roles} />
      </header>
      <nav className="tabs">
        {TABS.map(([k, label]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>)}
      </nav>
      <main className="wrap">
        {tab === "overview" && <Overview {...ctx} />}
        {tab === "results" && <Results />}
        {tab === "review" && <Review />}
        {tab === "blocked" && <Blocked {...ctx} />}
        {tab === "evaluation" && <Evaluation />}
        {tab === "triage" && <Triage {...ctx} />}
        {tab === "kb" && <KbRules {...ctx} />}
        {tab === "insights" && <Insights />}
      </main>
    </>
  );
}

function Auth({ session, roles }: { session: Session | null; roles: string[] }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(false);
  if (session) {
    return (
      <div className="auth">
        <span>{session.user.email}</span>
        <span className="muted">{roles.length ? roles.join(", ") : "no roles yet"}</span>
        <button className="ghost" onClick={() => sb.auth.signOut()}>Sign out</button>
      </div>
    );
  }
  if (!open) return <div className="auth"><span className="muted">Viewing read-only</span><button onClick={() => setOpen(true)}>Sign in</button></div>;
  const go = async (signup: boolean) => {
    setMsg("");
    const r = signup ? await sb.auth.signUp({ email, password: pw }) : await sb.auth.signInWithPassword({ email, password: pw });
    if (r.error) setMsg(r.error.message);
    else if (signup) setMsg("Account created. Confirm your email if asked, then ask the owner to grant you a role.");
  };
  return (
    <div className="auth">
      <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <input placeholder="password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
      <button onClick={() => go(false)}>Sign in</button>
      <button className="ghost" onClick={() => go(true)}>Create account</button>
      {msg && <span className="muted">{msg}</span>}
    </div>
  );
}
