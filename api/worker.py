"""POST /api/worker  Claim and process queued jobs.

Auth: a signed-in user with the operator role (Run AI Analysis button), or the
x-worker-secret header (the Supabase pg_cron sweeper).
Body: {"run_id": "<uuid or omitted>", "limit": 1-5}
"""
import os
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from _lib import db  # noqa: E402
from _lib.http import read_json, secret_ok, send_json, user_roles  # noqa: E402

TIME_BUDGET_SECONDS = 45


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        is_cron = secret_ok(self, "x-worker-secret", "WORKER_SECRET")
        if not is_cron and "operator" not in user_roles(self):
            return send_json(self, 401, {"error": "Sign in with an operator account, or send the worker secret."})
        body = read_json(self)
        run_id = body.get("run_id") or None
        limit = max(1, min(int(body.get("limit") or 2), 5))
        started = time.time()
        results, swept = [], 0
        try:
            conn = db.connect()
        except Exception as e:  # noqa: BLE001
            return send_json(self, 500, {"error": f"Database connection failed: {type(e).__name__}"})
        try:
            if is_cron and not run_id:
                swept = conn.execute("select sweep_unqueued_tickets() as n").fetchone()["n"]
                conn.commit()
            names = db.merchant_names(conn)
            conn.commit()
            while time.time() - started < TIME_BUDGET_SECONDS - 15 and len(results) < limit:
                jobs = db.claim(conn, run_id, 1)
                if not jobs:
                    break
                results.append(db.process_job(conn, jobs[0], use_claude=True, names=names))
            progress = db.run_progress(conn, run_id) if run_id else None
            conn.commit()
        finally:
            conn.close()
        return send_json(self, 200, {"processed": results, "swept_new_tickets": swept, "progress": progress})
