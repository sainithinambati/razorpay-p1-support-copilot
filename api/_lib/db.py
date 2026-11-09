"""Database access with the restricted ai_processor role (read sources, write AI tables only)."""
import json
import os
import traceback

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .pipeline import analyse, failed_row


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # prepare_threshold=None: required by the Supabase transaction pooler.
    return psycopg.connect(url, autocommit=False, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)


def _plain(v):
    if type(v).__name__ == "Decimal":
        f = float(v)
        return int(f) if f.is_integer() else f
    return v


def _row(r):
    return {k: _plain(v) for k, v in r.items()} if r else None


def log(conn, run_id, job_id, ticket_id, stage, message, level="info", details=None):
    conn.execute(
        "insert into ai_processing_logs (run_id, job_id, ticket_id, stage, level, message, details) values (%s,%s,%s,%s,%s,%s,%s)",
        (run_id, job_id, ticket_id, stage, level, message, Jsonb(details or {})))


def load_kb(conn, version_id):
    r = conn.execute("select rules_version, parameters, team_mapping, articles from kb_rule_versions where version_id = %s",
                     (version_id,)).fetchone()
    return dict(r)


def merchant_names(conn):
    return {r["merchant_id"]: r["business_name"] for r in conn.execute("select merchant_id, business_name from p1_merchants")}


def claim(conn, run_id, limit):
    rows = conn.execute("select * from claim_jobs(%s, %s, %s)", (run_id, limit, 150)).fetchall()
    conn.commit()
    return rows


def process_job(conn, job, use_claude=True, names=None):
    """Process one claimed job. Never raises: failures are recorded and retried within the budget."""
    run = conn.execute("select * from ai_analysis_runs where run_id = %s", (job["run_id"],)).fetchone()
    try:
        kb = load_kb(conn, run["kb_version_id"])
        ticket = _row(conn.execute("select * from p1_tickets where ticket_id = %s", (job["ticket_id"],)).fetchone())
        merchant = None
        if ticket.get("merchant_id"):
            merchant = _row(conn.execute("select * from p1_merchants where merchant_id = %s", (ticket["merchant_id"],)).fetchone())
        names = names or merchant_names(conn)
        log(conn, run["run_id"], job["job_id"], ticket["ticket_id"], "claim", f"Job claimed (attempt {job['attempts']} of {job['max_attempts']})")

        events = []
        res = analyse(ticket, merchant, kb, names, use_claude, events)
        for e in events:
            log(conn, run["run_id"], job["job_id"], ticket["ticket_id"], e["stage"], e["message"], e["level"], e["details"])

        row = res["row"]
        review = run["run_type"] == "review"
        inserted = conn.execute(
            """insert into ai_ticket_analysis (run_id, job_id, ticket_id, merchant_id, category, action, escalation_team,
                 risk_level, drafted_reply, secondary_topic, risk_signals, language_style, tone, confidence, classifier_source,
                 payment_id, refund_amount_inr, relevant_kb_ids, merchant_facts_used, decision_trace, escalation_team_primary,
                 escalation_team_secondary, validation_status, human_review_required, failure_reason, model_name, rules_version,
                 review_status)
               values (%(run_id)s, %(job_id)s, %(ticket_id)s, %(merchant_id)s, %(category)s, %(action)s, %(escalation_team)s,
                 %(risk_level)s, %(drafted_reply)s, %(secondary_topic)s, %(risk_signals)s, %(language_style)s, %(tone)s,
                 %(confidence)s, %(classifier_source)s, %(payment_id)s, %(refund_amount_inr)s, %(relevant_kb_ids)s,
                 %(merchant_facts_used)s, %(decision_trace)s, %(escalation_team_primary)s, %(escalation_team_secondary)s,
                 %(validation_status)s, %(human_review_required)s, %(failure_reason)s, %(model_name)s, %(rules_version)s,
                 %(review_status)s)
               on conflict (run_id, ticket_id) do nothing
               returning analysis_id""",
            {**row, "run_id": run["run_id"], "job_id": job["job_id"],
             "merchant_facts_used": Jsonb(row["merchant_facts_used"]), "decision_trace": Jsonb(row["decision_trace"]),
             "review_status": "pending_triage" if review else "not_required"}).fetchone()

        if inserted:
            aid = inserted["analysis_id"]
            b = res["blocked"]
            if b:
                conn.execute(
                    """insert into ai_blocked_drafts (analysis_id, run_id, ticket_id, failed_draft, unsafe_statement, reason,
                         violated_kb_id, fallback_action, required_team) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (aid, run["run_id"], ticket["ticket_id"], b["failed_draft"], b["unsafe_statement"], b["reason"],
                     b["violated_kb_id"], b["fallback_action"], b["required_team"]))
            s = res["kb_suggestion"]
            if s:
                conn.execute("insert into ai_kb_suggestions (run_id, ticket_id, kb_id, topic, suggestion) values (%s,%s,%s,%s,%s)",
                             (run["run_id"], ticket["ticket_id"], s["kb_id"], s["topic"], s["suggestion"]))
            if review and row["risk_level"] == "Critical":
                conn.execute(
                    "insert into ai_alerts (analysis_id, run_id, ticket_id, team, risk_signals, message) values (%s,%s,%s,%s,%s,%s)",
                    (aid, run["run_id"], ticket["ticket_id"], row["escalation_team"], row["risk_signals"],
                     f"Critical ticket {ticket['ticket_id']} ({row['category']}): immediate review by {row['escalation_team']}"))
                log(conn, run["run_id"], job["job_id"], ticket["ticket_id"], "alert", f"Critical alert raised for {row['escalation_team']}")

        conn.execute("update ai_jobs set status = 'done', lease_until = null, last_error = null, updated_at = now() where job_id = %s",
                     (job["job_id"],))
        conn.execute("update ai_analysis_runs set model_name = coalesce(model_name, %s) where run_id = %s",
                     (row["model_name"], run["run_id"]))
        conn.execute("select finish_run_if_done(%s)", (run["run_id"],))
        conn.commit()
        return {"ticket_id": ticket["ticket_id"], "status": "done", "action": row["action"]}
    except Exception as e:  # noqa: BLE001 - record any failure, keep the worker alive
        conn.rollback()
        return _record_failure(conn, job, run, e)


def _record_failure(conn, job, run, err):
    msg = f"{type(err).__name__}: {err}"
    try:
        final = job["attempts"] >= job["max_attempts"]
        log(conn, job["run_id"], job["job_id"], job["ticket_id"], "error", msg[:500], "error",
            {"trace": traceback.format_exc()[-1500:], "final_attempt": final})
        if final:
            kb = load_kb(conn, run["kb_version_id"])
            ticket = _row(conn.execute("select * from p1_tickets where ticket_id = %s", (job["ticket_id"],)).fetchone())
            row = failed_row(ticket, kb, msg)
            conn.execute(
                """insert into ai_ticket_analysis (run_id, job_id, ticket_id, merchant_id, category, action, escalation_team,
                     risk_level, drafted_reply, risk_signals, language_style, classifier_source, relevant_kb_ids,
                     merchant_facts_used, decision_trace, validation_status, human_review_required, failure_reason,
                     model_name, rules_version, review_status)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict (run_id, ticket_id) do nothing""",
                (job["run_id"], job["job_id"], row["ticket_id"], row["merchant_id"], row["category"], row["action"],
                 row["escalation_team"], row["risk_level"], row["drafted_reply"], row["risk_signals"], row["language_style"],
                 row["classifier_source"], row["relevant_kb_ids"], Jsonb({}), Jsonb(row["decision_trace"]),
                 "processing_failed", True, row["failure_reason"], None, row["rules_version"],
                 "pending_triage" if run["run_type"] == "review" else "not_required"))
            conn.execute("update ai_jobs set status = 'failed', lease_until = null, last_error = %s, updated_at = now() where job_id = %s",
                         (msg[:500], job["job_id"]))
        else:
            conn.execute("update ai_jobs set status = 'pending', lease_until = null, last_error = %s, updated_at = now() where job_id = %s",
                         (msg[:500], job["job_id"]))
        conn.execute("select finish_run_if_done(%s)", (job["run_id"],))
        conn.commit()
    except Exception:  # noqa: BLE001
        conn.rollback()
    return {"ticket_id": job["ticket_id"], "status": "failed" if job["attempts"] >= job["max_attempts"] else "retry", "error": msg[:200]}


def run_progress(conn, run_id):
    r = conn.execute("select * from v_run_progress where run_id = %s", (run_id,)).fetchone()
    return json.loads(json.dumps(r, default=str)) if r else None
