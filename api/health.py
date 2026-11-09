"""GET /api/health  Shows which settings are configured (never their values)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from _lib import claude, db  # noqa: E402
from _lib.http import send_json, supabase_anon, supabase_url  # noqa: E402
from _lib.taxonomy import AUTO_SEND_ENABLED  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        out = {
            "auto_send_enabled": AUTO_SEND_ENABLED,
            "claude_configured": claude.enabled(),
            "claude_model": claude.model_name(),
            "database_url_set": bool(os.environ.get("DATABASE_URL")),
            "supabase_url_set": bool(supabase_url()),
            "supabase_anon_key_set": bool(supabase_anon()),
            "worker_secret_set": bool(os.environ.get("WORKER_SECRET")),
            "webhook_secret_set": bool(os.environ.get("WEBHOOK_SECRET")),
        }
        try:
            conn = db.connect()
            r = conn.execute("select current_user as role, (select count(*) from p1_tickets) as tickets").fetchone()
            conn.close()
            out["database"] = {"connected": True, "role": r["role"], "tickets": r["tickets"]}
        except Exception as e:  # noqa: BLE001
            out["database"] = {"connected": False, "error": type(e).__name__}
        send_json(self, 200, out)
