"""Database tests (prompt Section 20, tests 1-12 and 28-36).

Run against a database built from supabase/migrations with:
  TEST_ADMIN_URL      superuser/owner connection (sets up users and roles)
  DATABASE_URL        the ai_processor connection (the real processing credentials)
Skipped when these are not set.
"""
import json
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

ADMIN = os.environ.get("TEST_ADMIN_URL")
PROC = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not (ADMIN and PROC), reason="TEST_ADMIN_URL and DATABASE_URL not set")


def admin():
    return psycopg.connect(ADMIN, autocommit=True, row_factory=dict_row)


def proc():
    return psycopg.connect(PROC, autocommit=True, row_factory=dict_row, prepare_threshold=None)


def as_user(conn, user_id):
    conn.execute("set role authenticated")
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(user_id),))


@pytest.fixture(scope="module")
def users():
    """operator/triage/editor = user A, approver = user B, nobody = user C."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with admin() as conn:
        for u in (a, b, c):
            conn.execute("insert into auth.users (id, email) values (%s, %s)", (u, f"{u}@test.local"))
        for role in ("operator", "triage_agent", "kb_editor"):
            conn.execute("insert into app_user_roles values (%s, %s)", (a, role))
        conn.execute("insert into app_user_roles values (%s, 'kb_approver')", (b,))
        conn.execute("insert into app_user_roles values (%s, 'kb_editor')", (b,))
    return {"a": a, "b": b, "c": c}


# 1-3 --------------------------------------------------------------------------------------------
def test_processing_role_reads_all_source_data():
    with proc() as conn:
        assert conn.execute("select current_user u").fetchone()["u"] == "ai_processor"
        assert conn.execute("select count(*) n from p1_tickets where ticket_id <= 'T-0150'").fetchone()["n"] == 150
        assert conn.execute("select count(*) n from p1_merchants").fetchone()["n"] == 60
        assert conn.execute("select count(*) n from p1_knowledgebase").fetchone()["n"] == 17


# 4-7 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "update p1_tickets set merchant_message = 'x' where ticket_id = 'T-0001'",
    "update p1_merchants set settlement_status = 'Settled' where merchant_id = 'M1007'",
    "update p1_knowledgebase set guidance = 'x' where kb_id = 'KB-07'",
    "delete from p1_tickets where ticket_id = 'T-0001'",
    "delete from p1_merchants where merchant_id = 'M1001'",
    "delete from p1_knowledgebase where kb_id = 'KB-01'",
    "insert into p1_tickets values ('T-9999', 'x', 'Chat', null, 'x')",
    "insert into p1_merchants (merchant_id, business_name) values ('M9999', 'x')",
    "truncate p1_tickets",
    "alter table p1_tickets add column ai_status text",
])
def test_processing_role_cannot_modify_sources(sql):
    with proc() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            conn.execute(sql)
    print(f"REJECTED BY DATABASE: {sql!r} -> {e.value.diag.message_primary}")


def test_even_the_owner_cannot_change_source_rows():
    with admin() as conn:
        for sql in ("update p1_tickets set channel = 'Email' where ticket_id = 'T-0002'",
                    "delete from p1_knowledgebase where kb_id = 'KB-15'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
        rows = {r["table_name"]: r for r in conn.execute("select * from source_integrity()")}
        assert all(r["original_rows_unchanged"] for r in rows.values())


def test_anon_cannot_write_anything():
    with admin() as conn:
        conn.execute("set role anon")
        for sql in ("insert into ai_ticket_analysis (run_id) values (gen_random_uuid())",
                    "update kb_rule_versions set status = 'retired'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)


# 8-11 Runs, jobs, history ----------------------------------------------------------------------------
def start_run(user, mode):
    with admin() as conn:
        as_user(conn, user)
        return conn.execute("select start_run(%s) r", (mode,)).fetchone()["r"]


def drain(run_id, only=None):
    from _lib import db
    conn = db.connect()
    names = db.merchant_names(conn)
    conn.commit()
    out = []
    while True:
        jobs = db.claim(conn, run_id, 1)
        if not jobs:
            break
        out.append(db.process_job(conn, jobs[0], use_claude=False, names=names))
    conn.close()
    return out


@pytest.fixture(scope="module", autouse=True)
def high_run_cap():
    """The public daily cap protects API credit in production; tests start many runs."""
    with admin() as conn:
        conn.execute("insert into app_config (key, value) values ('public_runs_per_day', '1000') "
                     "on conflict (key) do update set value = excluded.value")
    yield


def test_public_run_guards():
    """Anyone may start a run, but only one at a time and at most N per day."""
    with admin() as conn:
        conn.execute("set role anon")
        run = conn.execute("select start_run('all') r").fetchone()["r"]
        with pytest.raises(psycopg.errors.RaiseException, match="already in progress"):
            conn.execute("select start_run('all')")
        conn.execute("reset role")
        conn.execute("delete from ai_jobs where run_id = %s", (run,))
        n = conn.execute("select count(*) n from ai_analysis_runs where run_type = 'batch' and created_by is null "
                         "and created_at > now() - interval '24 hours'").fetchone()["n"]
        conn.execute("update app_config set value = %s where key = 'public_runs_per_day'", (str(n),))
        conn.execute("set role anon")
        with pytest.raises(psycopg.errors.RaiseException, match="daily limit"):
            conn.execute("select start_run('all')")
        conn.execute("reset role")
        conn.execute("update app_config set value = '1000' where key = 'public_runs_per_day'")


def test_full_run_writes_one_row_per_ticket_and_rerun_keeps_history(users):
    run1 = start_run(users["a"], "all")
    res = drain(run1)
    assert len(res) == 150 and all(r["status"] == "done" for r in res)
    run2 = start_run(users["a"], "all")
    drain(run2)
    with proc() as conn:
        n1 = conn.execute("select count(*) n from ai_ticket_analysis where run_id = %s", (run1,)).fetchone()["n"]
        n2 = conn.execute("select count(*) n from ai_ticket_analysis where run_id = %s", (run2,)).fetchone()["n"]
        assert n1 == 150 and n2 == 150  # history preserved, new run reprocessed every ticket
        assert conn.execute("select count(*) n from v_submission_table").fetchone()["n"] == 150
        st = conn.execute("select status from ai_analysis_runs where run_id = %s", (run1,)).fetchone()["status"]
        assert st == "completed"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from ai_ticket_analysis where run_id = %s", (run1,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update ai_ticket_analysis set action = 'AUTO_RESOLVE' where run_id = %s", (run1,))


def test_duplicate_job_in_same_run_is_prevented(users):
    with admin() as conn:
        run_id = conn.execute("select run_id from ai_analysis_runs where run_type = 'batch' limit 1").fetchone()["run_id"]
    with proc() as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("insert into ai_jobs (run_id, ticket_id) values (%s, 'T-0001')", (run_id,))


def test_concurrent_claims_never_overlap(users):
    run_id = start_run(users["a"], "all")
    c1, c2 = proc(), proc()
    c1.autocommit = c2.autocommit = False
    a = c1.execute("select job_id from claim_jobs(%s, 5, 150)", (run_id,)).fetchall()
    b = c2.execute("select job_id from claim_jobs(%s, 5, 150)", (run_id,)).fetchall()  # c1 not yet committed
    c1.commit(); c2.commit()
    assert a and b and not ({r["job_id"] for r in a} & {r["job_id"] for r in b})
    with admin() as conn:  # expire the test leases so the run can finish (one run at a time)
        conn.execute("update ai_jobs set lease_until = now() - interval '1 second' where run_id = %s and status = 'processing'", (run_id,))
    drain(run_id)


def test_retry_budget_is_capped_at_three():
    with proc() as conn:
        assert conn.execute("select max(max_attempts) m from ai_jobs").fetchone()["m"] <= 3
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("insert into ai_jobs (run_id, ticket_id, max_attempts) select run_id, 'T-0002', 4 from ai_analysis_runs limit 1")


def test_remaining_mode_has_nothing_left(users):
    with admin() as conn:
        as_user(conn, users["a"])
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("select start_run('remaining')")


def test_batch_runs_pinned_to_baseline():
    with proc() as conn:
        assert {r["rules_version"] for r in conn.execute("select rules_version from ai_analysis_runs where run_type='batch'")} == {"kb-v1"}


# 12 + 28-30 Webhook / review mode / triage --------------------------------------------------------------
def submit(user, message, merchant="M1015", channel="Chat"):
    with admin() as conn:
        as_user(conn, user)
        return conn.execute("select submit_ticket(%s, %s, %s) t", (merchant, channel, message)).fetchone()["t"]


class FakeHandler:
    def __init__(self, body, secret):
        self.headers = {"x-webhook-secret": secret, "Content-Length": str(len(body))}
        import io
        self.rfile, self.wfile, self.status = io.BytesIO(body), io.BytesIO(), None

    def send_response(self, s): self.status = s
    def send_header(self, *a): pass
    def end_headers(self): pass


def call_webhook(ticket_id, monkeypatch, check_before_200=None):
    import webhook as wh
    from _lib import db
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    seen = {}
    real = db.process_job

    def spy(conn, job, use_claude=True, names=None):
        with proc() as c:  # independent connection: the job must already be committed
            seen["job_visible"] = c.execute("select count(*) n from ai_jobs where job_id = %s", (job["job_id"],)).fetchone()["n"]
        return real(conn, job, use_claude=False, names=names)
    monkeypatch.setattr(db, "process_job", spy)
    body = json.dumps({"type": "INSERT", "table": "p1_tickets", "record": {"ticket_id": ticket_id}}).encode()
    h = FakeHandler(body, "s3cret")
    wh.handler.do_POST(h)
    return h.status, json.loads(h.wfile.getvalue()), seen


def test_webhook_job_is_durable_before_200_and_dedupes(users, monkeypatch):
    tid = submit(users["a"], "Webhooks are not reaching my server since yesterday. M1015")
    status, body, seen = call_webhook(tid, monkeypatch)
    assert status == 200 and seen["job_visible"] == 1
    status2, body2, _ = call_webhook(tid, monkeypatch)  # duplicate delivery
    assert status2 == 200 and body2["duplicate"] is True and body2["job_id"] == body["job_id"]
    with proc() as conn:
        assert conn.execute("select count(*) n from ai_jobs where ticket_id = %s", (tid,)).fetchone()["n"] == 1
        row = conn.execute("select review_status from ai_ticket_analysis where ticket_id = %s", (tid,)).fetchone()
        assert row["review_status"] == "pending_triage"


def test_webhook_rejects_bad_secret(monkeypatch):
    import webhook as wh
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    h = FakeHandler(b"{}", "wrong")
    wh.handler.do_POST(h)
    assert h.status == 401


def test_critical_new_ticket_raises_alert_and_triage_flow(users, monkeypatch):
    tid = submit(users["a"], "Someone has logged into my dashboard and changed my bank account. I did not do this. M1015")
    call_webhook(tid, monkeypatch)
    with proc() as conn:
        a = conn.execute("select * from ai_ticket_analysis where ticket_id = %s", (tid,)).fetchone()
        al = conn.execute("select * from ai_alerts where ticket_id = %s", (tid,)).fetchone()
    assert a["risk_level"] == "Critical" and a["escalation_team"] == "Security" and al["team"] == "Security"
    assert al["acknowledged_at"] is None  # raised immediately, before any triage action
    with admin() as conn:
        as_user(conn, users["a"])
        with pytest.raises(psycopg.errors.RaiseException):  # critical cannot be corrected to auto-resolve
            conn.execute("select triage_decide(%s, 'corrected', %s, 'try')", (a["analysis_id"], json.dumps({"action": "AUTO_RESOLVE"})))
        conn.execute("select triage_decide(%s, 'corrected', %s, 'Also legal exposure')",
                     (a["analysis_id"], json.dumps({"risk_level": "Critical", "escalation_team": "Legal"})))
    with proc() as conn:
        a2 = conn.execute("select * from ai_ticket_analysis where analysis_id = %s", (a["analysis_id"],)).fetchone()
        tr = conn.execute("select * from v_triage_queue where ticket_id = %s", (tid,)).fetchone()
    assert a2["escalation_team"] == "Security" and a2["review_status"] == "routed"  # AI suggestion unchanged
    assert tr["final_escalation_team"] == "Legal" and tr["triage_decision"] == "corrected"


def test_non_triage_user_cannot_decide(users, monkeypatch):
    tid = submit(users["a"], "I want to change my registered email and mobile number on the account. How? M1015")
    call_webhook(tid, monkeypatch)
    with proc() as conn:
        aid = conn.execute("select analysis_id from ai_ticket_analysis where ticket_id = %s", (tid,)).fetchone()["analysis_id"]
    with admin() as conn:
        as_user(conn, users["c"])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select triage_decide(%s, 'confirmed', '{}', null)", (aid,))


def test_sweeper_picks_up_lost_webhooks(users):
    tid = submit(users["a"], "KYC me kya kya documents lagenge? M1015")
    with proc() as conn:
        assert conn.execute("select sweep_unqueued_tickets() n").fetchone()["n"] >= 1
        assert conn.execute("select count(*) n from ai_jobs where ticket_id = %s", (tid,)).fetchone()["n"] == 1
    drain(None)


# 31-36 KB governance --------------------------------------------------------------------------------------
def base_version():
    with proc() as conn:
        return conn.execute("select * from kb_rule_versions where is_baseline").fetchone()


def test_ai_role_cannot_write_kb_versions():
    with proc() as conn:
        for sql in ("update kb_rule_versions set status = 'retired'",
                    "insert into kb_rule_versions (rules_version, status, articles, parameters, team_mapping) values ('x','draft','{}','{}','{}')",
                    "select kb_approve(1)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)


def test_baseline_cannot_be_edited_or_deleted():
    with admin() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update kb_rule_versions set parameters = '{}' where is_baseline")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from kb_rule_versions where is_baseline")


def test_kb_change_flow_and_four_eyes(users):
    b = base_version()
    params = dict(b["parameters"], high_value_refund_inr=10000)
    with admin() as conn:
        as_user(conn, users["a"])
        vid = conn.execute("select kb_create_draft(%s, %s, %s, %s, 'Lower refund approval threshold for test') v",
                           (b["version_id"], json.dumps(b["articles"]), json.dumps(params), json.dumps(b["team_mapping"]))).fetchone()["v"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select kb_approve(%s)", (vid,))  # user A is not an approver
    with admin() as conn:
        as_user(conn, users["b"])
        conn.execute("select kb_approve(%s)", (vid,))
        conn.execute("select kb_activate(%s)", (vid,))
    # Same person cannot approve their own draft
    with admin() as conn:
        as_user(conn, users["b"])
        vid2 = conn.execute("select kb_create_draft(%s, %s, %s, %s, 'Self approval attempt') v",
                            (b["version_id"], json.dumps(b["articles"]), json.dumps(dict(params, chargeback_evidence_days=10)),
                             json.dumps(b["team_mapping"]))).fetchone()["v"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select kb_approve(%s)", (vid2,))
    # New review ticket uses the new version; the decision changes (Rs 12,000 >= 10,000)
    tid = submit(users["a"], "Customer ka refund Rs 12,000 abhi tak nahi mila, 3 din ho gaye. pay_NPQNDM4PXUUM2N. M1015")
    with proc() as conn:
        conn.execute("select sweep_unqueued_tickets()")
    drain(None)
    with proc() as conn:
        row = conn.execute("select * from ai_ticket_analysis where ticket_id = %s", (tid,)).fetchone()
    assert row["rules_version"] == f"kb-v{vid}" and row["escalation_team"] == "Refund Ops"
    # Batch runs still use kb-v1
    run = start_run(users["a"], "all")
    with proc() as conn:
        assert conn.execute("select rules_version from ai_analysis_runs where run_id = %s", (run,)).fetchone()["rules_version"] == "kb-v1"
    drain(run)
    # Roll back to baseline
    with admin() as conn:
        as_user(conn, users["b"])
        conn.execute("select kb_activate(%s)", (b["version_id"],))


@pytest.mark.parametrize("bad", [{"auto_send_enabled": True}, {"critical_gate_enabled": False}])
def test_safety_floors_cannot_be_disabled(users, bad):
    b = base_version()
    with admin() as conn:
        as_user(conn, users["a"])
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("select kb_create_draft(%s, %s, %s, %s, 'Try to disable a safety rule')",
                         (b["version_id"], json.dumps(b["articles"]), json.dumps(dict(b["parameters"], **bad)), json.dumps(b["team_mapping"])))


def test_critical_routes_cannot_be_pointed_elsewhere(users):
    b = base_version()
    with admin() as conn:
        as_user(conn, users["a"])
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("select kb_create_draft(%s, %s, %s, %s, 'Route legal to tech support')",
                         (b["version_id"], json.dumps(b["articles"]), json.dumps(b["parameters"]),
                          json.dumps(dict(b["team_mapping"], legal="Tech Support"))))
