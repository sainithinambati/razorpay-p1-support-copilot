"""GET /api/export?format=csv|xlsx  The mandatory 150-ticket submission table."""
import csv
import io
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
from _lib import db  # noqa: E402
from _lib.http import send_json  # noqa: E402

COLUMNS = ["ticket_id", "category", "action", "escalation_team", "risk_level", "drafted_reply"]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        fmt = (parse_qs(urlparse(self.path).query).get("format") or ["csv"])[0]
        try:
            conn = db.connect()
            rows = conn.execute("select * from v_submission_table").fetchall()
            conn.close()
        except Exception as e:  # noqa: BLE001
            return send_json(self, 500, {"error": f"Database error: {type(e).__name__}"})
        if fmt == "xlsx":
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font
            wb = Workbook()
            ws = wb.active
            ws.title = "P1_Results"
            ws.append(COLUMNS)
            for c in ws[1]:
                c.font = Font(bold=True)
            for r in rows:
                ws.append([r[c] for c in COLUMNS])
            for col, w in zip("ABCDEF", [10, 20, 15, 26, 11, 100]):
                ws.column_dimensions[col].width = w
            for row in ws.iter_rows(min_row=2):
                row[5].alignment = Alignment(wrap_text=True, vertical="top")
            buf = io.BytesIO()
            wb.save(buf)
            data, ctype, name = buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "p1_results.xlsx"
        else:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            data, ctype, name = ("﻿" + buf.getvalue()).encode(), "text/csv; charset=utf-8", "p1_results.csv"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("X-Row-Count", str(len(rows)))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
