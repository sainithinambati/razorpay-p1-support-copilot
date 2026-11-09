"""Small helpers for Vercel Python functions."""
import hmac
import json
import os
import urllib.request


def send_json(handler, status, payload):
    body = json.dumps(payload, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    n = int(handler.headers.get("Content-Length") or 0)
    if not n:
        return {}
    try:
        return json.loads(handler.rfile.read(n))
    except json.JSONDecodeError:
        return {}


def secret_ok(handler, header, env_name):
    expected = os.environ.get(env_name)
    got = handler.headers.get(header)
    return bool(expected) and bool(got) and hmac.compare_digest(expected, got)


def supabase_url():
    return os.environ.get("SUPABASE_URL") or os.environ.get("VITE_SUPABASE_URL")


def supabase_anon():
    return os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY")


def user_roles(handler):
    """Verify the caller's Supabase session and return their roles (checked inside the database)."""
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not supabase_url() or not supabase_anon():
        return []
    req = urllib.request.Request(
        supabase_url().rstrip("/") + "/rest/v1/rpc/my_roles", data=b"{}", method="POST",
        headers={"apikey": supabase_anon(), "Authorization": auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            roles = json.loads(r.read())
            return roles if isinstance(roles, list) else []
    except Exception:  # noqa: BLE001
        return []
