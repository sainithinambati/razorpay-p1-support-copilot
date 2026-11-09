"""Load the Data Pack and the baseline KB (kb-v1) exactly as seeded in Supabase."""
import os
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

BASELINE_PARAMETERS = {
    "settlement_tplus_days": 2, "bank_delay_extra_business_days": 2, "risk_review_max_business_days": 5,
    "kyc_release_business_days": 1, "penny_drop_hours": 24, "payment_success_threshold_pct": 85,
    "auto_reversal_business_days": "5-7", "refund_credit_business_days": "5-7", "arn_available_after_days": 3,
    "high_value_refund_inr": 100000, "chargeback_evidence_days": 7, "invoice_generation_day": 5,
    "kyc_reverification_months": 24, "webhook_timeout_seconds": 5, "webhook_retry_hours": 24,
    "confidence_review_threshold": 0.6,
}
BASELINE_TEAMS = {
    "legal": "Legal", "security": "Security", "identity_conflict": "Security", "staff_misconduct": "Risk Ops",
    "settlement_hold": "Risk Ops", "payment_degradation": "Payments Ops", "high_value_refund": "Refund Ops",
    "pricing": "Account Manager", "closure": "Account Manager + Risk Ops", "tech_fault": "Tech Support",
}


def load_pack():
    wb = openpyxl.load_workbook(os.path.join(ROOT, "data", "Buildathon_Student_Data_Pack.xlsx"), read_only=True)

    def rows(name):
        it = wb[name].iter_rows(values_only=True)
        hdr = next(it)
        return [dict(zip(hdr, r)) for r in it if r and r[0]]

    tickets = rows("P1_Tickets")
    merchants = {m["merchant_id"]: m for m in rows("P1_Merchants")}
    for m in merchants.values():
        for k in ("open_chargebacks", "refunds_in_process"):
            if m.get(k) is not None:
                m[k] = int(m[k])
    kb_rows = rows("P1_KnowledgeBase")
    kb = {"rules_version": "kb-v1", "parameters": BASELINE_PARAMETERS, "team_mapping": BASELINE_TEAMS,
          "articles": {r["kb_id"]: {"topic": r["topic"], "guidance": r["guidance"]} for r in kb_rows}}
    return tickets, merchants, kb


def run_all(use_claude=False):
    from _lib.pipeline import analyse
    tickets, merchants, kb = load_pack()
    names = {k: v["business_name"] for k, v in merchants.items()}
    out = []
    for t in tickets:
        events = []
        res = analyse(t, merchants.get(t["merchant_id"]), kb, names, use_claude, events)
        res["events"] = events
        out.append(res)
    return out
