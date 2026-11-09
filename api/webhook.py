"""POST /api/webhook  Supabase Database Webhook for new rows in p1_tickets.

The durable job is created and claimed (committed) BEFORE any 200 response.
Duplicate deliveries for the same ticket reuse the existing job.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from _lib import db  # noqa: E402
from _lib.http import read_json, secret_ok, send_json  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not secret_ok(self, "x-webhook-secret", "WEBHOOK_SECRET"):
            return send_json(self, 401, {"error": "Invalid webhook secret"})
        body = read_json(self)
        record = body.get("record") or {}
        if body.get("type") != "INSERT" or body.get("table") != "p1_tickets" or not record.get("ticket_id"):
            return send_json(self, 200, {"ignored": True})
        try:
            conn = db.connect()
        except Exception as e:  # noqa: BLE001
            return send_json(self, 500, {"error": f"Database connection failed: {type(e).__name__}"})
        try:
            # 1-3: create or confirm the durable job, store run + ticket IDs, claim it atomically.
            job = conn.execute("select * from create_review_job(%s, 'webhook')", (record["ticket_id"],)).fetchone()
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            conn.close()
            return send_json(self, 500, {"error": f"Could not create job: {e}"[:300]})
        result = {"ticket_id": record["ticket_id"], "job_id": job["job_id"], "status": job["status"], "duplicate": not job["newly_claimed"]}
        try:
            if job["newly_claimed"]:
                full = conn.execute("select * from ai_jobs where job_id = %s", (job["job_id"],)).fetchone()
                result["processing"] = db.process_job(conn, full, use_claude=True)
        finally:
            conn.close()
        # The job is durable whatever happened above; the sweeper retries unfinished jobs.
        return send_json(self, 200, result)
