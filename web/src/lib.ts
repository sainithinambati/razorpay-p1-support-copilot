import { createClient, Session } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string;
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY as string;
export const configured = Boolean(url && anon);
export const sb = createClient(url || "http://localhost", anon || "missing");

export const CATEGORIES = ["Settlement", "Payment Failure", "Refund", "Chargeback", "Fees & Pricing", "KYC", "Account Change",
  "Account Closure", "API & Integration", "Legal / Regulatory", "Security", "Staff Misconduct", "Unclear"];
export const ACTIONS = ["AUTO_RESOLVE", "ASK_FOR_INFO", "ESCALATE"];
export const RISKS = ["Critical", "High", "Medium", "Low"];
export const TEAMS = ["Legal", "Security", "Risk Ops", "Payments Ops", "Refund Ops", "Account Manager", "Account Manager + Risk Ops", "Tech Support"];

export async function api(path: string, session: Session | null, body?: unknown) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data: any = null;
  try { data = JSON.parse(text); } catch { data = { error: text.slice(0, 200) }; }
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

export async function fetchAll<T = any>(table: string, select = "*", build?: (q: any) => any): Promise<T[]> {
  const out: T[] = [];
  for (let from = 0; ; from += 1000) {
    let q = sb.from(table).select(select).range(from, from + 999);
    if (build) q = build(q);
    const { data, error } = await q;
    if (error) throw error;
    out.push(...(data as T[]));
    if (!data || data.length < 1000) break;
  }
  return out;
}

export const fmtTime = (s?: string) => (s ? new Date(s).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "");
export const pct = (n: number, d: number) => (d ? `${((100 * n) / d).toFixed(1)}%` : "n/a");
