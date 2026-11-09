"""Deterministic rule tests (prompt Section 20, tests 13-27). Run: python -m pytest -q"""
import copy
import csv
import os

import pytest

from fixtures import ROOT, load_pack, run_all
from _lib import claude
from _lib.pipeline import analyse
from _lib.validate import validate_reply
from _lib.engine import decide
from _lib.text import extract, detect_language, format_inr, mask_pii

TICKETS, MERCHANTS, KB = load_pack()
NAMES = {k: v["business_name"] for k, v in MERCHANTS.items()}
BY_ID = {t["ticket_id"]: t for t in TICKETS}


@pytest.fixture(scope="module")
def results():
    return {r["row"]["ticket_id"]: r for r in run_all()}


def run_one(ticket, kb=KB, merchant="default"):
    m = MERCHANTS.get(ticket.get("merchant_id")) if merchant == "default" else merchant
    return analyse(ticket, m, kb, NAMES, False, [])


# 26. Every ticket appears exactly once, nothing blank ---------------------------------------
def test_every_ticket_has_a_complete_row(results):
    assert len(results) == 150
    for tid, r in results.items():
        row = r["row"]
        for f in ("category", "action", "risk_level", "drafted_reply"):
            assert row[f], f"{tid} has blank {f}"
        assert row["action"] in ("AUTO_RESOLVE", "ASK_FOR_INFO", "ESCALATE")
        assert (row["action"] == "ESCALATE") == bool(row["escalation_team"])


# 13. Missing IDs -> ASK_FOR_INFO with a real category ----------------------------------------
@pytest.mark.parametrize("tid", ["T-0041", "T-0064", "T-0071", "T-0075", "T-0102", "T-0129"])
def test_missing_id_asks_for_info(results, tid):
    row = results[tid]["row"]
    assert row["action"] == "ASK_FOR_INFO"
    assert row["category"]
    assert "merchant ID" in row["drafted_reply"] or "merchant ID" in row["drafted_reply"].replace("Merchant", "merchant")


# 14. Critical message with missing ID still escalates ------------------------------------------
def test_critical_with_missing_id_escalates():
    t = {"ticket_id": "T-9001", "created_at": "01 Oct 2026 10:00", "channel": "Email", "merchant_id": None,
         "merchant_message": "I will send a legal notice and go to consumer court."}
    row = run_one(t, merchant=None)["row"]
    assert row["action"] == "ESCALATE" and row["escalation_team"] == "Legal" and row["risk_level"] == "Critical"


# 15. Critical cases never auto-resolve ------------------------------------------------------------
@pytest.mark.parametrize("tid,team", [("T-0002", "Security"), ("T-0021", "Legal"), ("T-0027", "Legal"), ("T-0030", "Security"),
                                      ("T-0031", "Legal"), ("T-0060", "Security"), ("T-0078", "Legal"), ("T-0115", "Legal"),
                                      ("T-0138", "Risk Ops")])
def test_critical_routes(results, tid, team):
    row = results[tid]["row"]
    assert row["action"] == "ESCALATE" and row["risk_level"] == "Critical" and row["escalation_team"] == team


def test_claude_cannot_downgrade_critical():
    t = BY_ID["T-0027"]
    ext = extract(t["merchant_message"])
    cls = {"category": "Settlement", "risk_signals": [], "confidence": 0.99, "source": "claude"}
    d = decide(t, MERCHANTS[t["merchant_id"]], cls, ext, KB["parameters"], KB["team_mapping"], "Legal / Regulatory", ["legal"])
    assert d.action == "ESCALATE" and d.risk_level == "Critical"


def test_confidence_never_overrides_rules():
    t = BY_ID["T-0014"]
    cls = {"category": "Payment Failure", "risk_signals": [], "confidence": 0.05, "source": "claude"}
    d = decide(t, MERCHANTS[t["merchant_id"]], cls, extract(t["merchant_message"]), KB["parameters"], KB["team_mapping"],
               "Payment Failure", [])
    assert d.action == "ESCALATE" and d.escalation_team == "Payments Ops" and d.human_review_required


# 16-17. Payment success rule ---------------------------------------------------------------------
@pytest.mark.parametrize("tid", ["T-0014", "T-0023", "T-0045", "T-0069", "T-0080", "T-0098", "T-0103", "T-0124", "T-0135", "T-0140"])
def test_low_success_routes_to_payments_ops(results, tid):
    assert results[tid]["row"]["escalation_team"] == "Payments Ops"


@pytest.mark.parametrize("tid", ["T-0113", "T-0125", "T-0061", "T-0118"])
def test_85_rule_does_not_fire_on_non_payment_tickets(results, tid):
    assert results[tid]["row"]["escalation_team"] != "Payments Ops"


# 18-19. Refund rules ------------------------------------------------------------------------------
@pytest.mark.parametrize("tid", ["T-0015", "T-0040", "T-0042", "T-0117", "T-0137", "T-0149"])
def test_high_value_refund_to_refund_ops(results, tid):
    row = results[tid]["row"]
    assert row["escalation_team"] == "Refund Ops" and row["refund_amount_inr"] >= 100000


def test_refund_without_payment_id_asks():
    t = {"ticket_id": "T-9002", "created_at": "01 Oct 2026 10:00", "channel": "Chat", "merchant_id": "M1005",
         "merchant_message": "Refund of Rs 2,000 for my customer not received yet. M1005"}
    row = run_one(t)["row"]
    assert row["action"] == "ASK_FOR_INFO" and row["category"] == "Refund"


def test_refund_threshold_reads_active_kb_version():
    """Test 34: a parameter change in a new KB version changes the decision."""
    kb2 = copy.deepcopy(KB)
    kb2["rules_version"] = "kb-v2"
    kb2["parameters"]["high_value_refund_inr"] = 10000
    row = run_one(BY_ID["T-0005"], kb=kb2)["row"]  # Rs 12,000
    assert row["escalation_team"] == "Refund Ops" and row["rules_version"] == "kb-v2"
    assert run_one(BY_ID["T-0005"])["row"]["action"] == "AUTO_RESOLVE"


# 20. Holds -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tid", ["T-0016", "T-0063", "T-0083", "T-0092"])
def test_risk_and_reserve_holds_to_risk_ops(results, tid):
    row = results[tid]["row"]
    assert row["escalation_team"] == "Risk Ops"
    assert "risk" not in row["drafted_reply"].lower() and "reserve" not in row["drafted_reply"].lower()


# 21. Pricing ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tid", ["T-0003", "T-0044", "T-0101", "T-0111"])
def test_pricing_to_account_manager(results, tid):
    assert results[tid]["row"]["escalation_team"] == "Account Manager"


# 22. Closure ------------------------------------------------------------------------------------------
def test_closure_checks_obligations(results):
    row = results["T-0070"]["row"]
    assert row["escalation_team"] == "Account Manager + Risk Ops"
    assert set(row["merchant_facts_used"]) == {"settlement_status", "refunds_in_process", "open_chargebacks"}
    assert "cannot be done immediately" in row["drafted_reply"]


# 23. Tech faults ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("tid", ["T-0008", "T-0047", "T-0118", "T-0145"])
def test_tech_faults_to_tech_support(results, tid):
    assert results[tid]["row"]["escalation_team"] == "Tech Support"


# Delayed settlements use only the system date -------------------------------------------------------------
def test_delayed_settlement_uses_system_date(results):
    for tid, r in results.items():
        row = r["row"]
        m = MERCHANTS.get(row["merchant_id"] or "")
        if row["category"] == "Settlement" and m and m["settlement_status"] == "Delayed" and row["action"] == "AUTO_RESOLVE":
            assert m["expected_settlement_date"] in row["drafted_reply"], tid
            assert "today" not in row["drafted_reply"].lower()


# 24. Language -------------------------------------------------------------------------------------------
def test_reply_language_matches_merchant(results):
    for tid, r in results.items():
        assert detect_language(r["row"]["drafted_reply"]) == r["row"]["language_style"], tid


# 25. Unsafe drafts are blocked -------------------------------------------------------------------------------
UNSAFE = [
    ("T-0039", "Hi,\n\nYour settlement will be credited by 30 Sep 2026. Regards", "invented"),
    ("T-0107", "Hi,\n\nYour settlement will be released today, don't worry. Regards", "time_promise"),
    ("T-0015", "Hi,\n\nYour refund of Rs. 2,50,000 will be refunded within 5-7 business days. Regards", "invented"),
    ("T-0003", "Hi,\n\nGood news, your waiver has been approved for this month. Regards", "promise"),
    ("T-0016", "Hi,\n\nYour account is under risk review by our Risk Ops team because of chargebacks. Regards", "internal_risk_disclosure"),
    ("T-0030", "Hi,\n\nWe apologize for the breach, it was our mistake. The issue has been resolved. Regards", "fault_admission"),
    ("T-0027", "Hi,\n\nYou are wrong, threats will not help. Regards", "argument"),
    ("T-0052", "Hi,\n\nPlease share your key secret with us so we can debug. Regards", "secret_request"),
    ("T-0009", "Hi,\n\nThe customer will get a refund in 5-7 business days. Regards", "reversal_called_refund"),
    ("T-0018", "Hi,\n\nYour payout is fine, unlike Kaveri Studio (M1020) which is on hold. Regards", "other_merchant_data"),
]


@pytest.mark.parametrize("tid,draft,rule", UNSAFE)
def test_unsafe_drafts_are_blocked(results, tid, draft, rule):
    t = BY_ID[tid]
    r = results[tid]
    from _lib.engine import Decision
    d = Decision(r["row"]["category"], r["row"]["action"], r["row"]["escalation_team"], r["row"]["risk_level"], "x", [])
    base = r["row"]["drafted_reply"]
    v = validate_reply(draft, base, d, t, MERCHANTS.get(t["merchant_id"]), NAMES, r["row"]["language_style"])
    assert any(rule in x.rule for x in v), [x.rule for x in v]


def test_unsafe_claude_draft_replaced_by_safe_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(claude, "classify", lambda m, c: {"category": "Settlement", "risk_signals": [], "confidence": 0.9,
                                                          "source": "claude", "language_style": "English", "tone": "neutral"})
    monkeypatch.setattr(claude, "polish", lambda fs, base: base.replace("02 Oct 2026", "tomorrow"))
    res = analyse(BY_ID["T-0039"], MERCHANTS["M1003"], KB, NAMES, True, [])
    assert res["blocked"] is not None
    assert res["row"]["validation_status"] == "failed_fallback" and res["row"]["human_review_required"]
    assert "tomorrow" not in res["row"]["drafted_reply"] and "02 Oct 2026" in res["row"]["drafted_reply"]


def test_classification_outside_taxonomy_is_rejected():
    with pytest.raises(ValueError):
        claude._check_classification({"category": "Billing", "risk_signals": [], "language_style": "English",
                                      "tone": "neutral", "confidence": 0.9, "candidate_kb_ids": []})


def test_claude_failure_falls_back_to_keywords_and_review(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def boom(*a, **k):
        raise claude.ClaudeError("timeout")
    monkeypatch.setattr(claude, "classify", boom)
    monkeypatch.setattr(claude, "polish", boom)
    row = analyse(BY_ID["T-0014"], MERCHANTS["M1058"], KB, NAMES, True, [])["row"]
    assert row["classifier_source"] == "keyword_fallback" and row["human_review_required"]
    assert row["escalation_team"] == "Payments Ops"


# Helpers ------------------------------------------------------------------------------------------------------
def test_extraction_and_formatting():
    e = extract("Please tell me the status of refund for pay_JXPRKE87BC2CGG, amount Rs 250,000, started 6 days back. M1009")
    assert e["payment_id"] == "pay_JXPRKE87BC2CGG" and e["amount_inr"] == 250000 and e["days_elapsed"] == 6
    assert format_inr(250000) == "2,50,000" and format_inr(5200000) == "52,00,000"
    assert "[EMAIL]" in mask_pii("mail me at a.b@gmail.com") and "[PHONE]" in mask_pii("call 9876543210")


def test_all_template_drafts_pass_validation(results):
    assert all(r["row"]["validation_status"] == "passed" for r in results.values())


# Evaluation set (offline, deterministic path) -----------------------------------------------------------------------
def test_evaluation_set_offline(results):
    labels = list(csv.DictReader(open(os.path.join(ROOT, "eval", "labels.csv"))))
    assert len(labels) >= 25
    misses = []
    for l in labels:
        row = results[l["ticket_id"]]["row"]
        if (row["category"], row["action"], row["escalation_team"] or "", row["risk_level"]) != \
                (l["expected_category"], l["expected_action"], l["expected_team"], l["expected_risk_level"]):
            misses.append((l["ticket_id"], row["category"], row["action"], row["escalation_team"], row["risk_level"]))
    assert not misses, misses


# Real Claude drafts that were wrongly blocked in the first live run (must now pass) --------------------------
SAFE_CLAUDE_DRAFTS = [
    ("T-0010", "Namaste Mosaic Stores team,\n\nHumse sampark karne ke liye dhanyavaad. Aapka KYC status abhi Verified hai. KYC ke liye ye documents chahiye hote hain: business ya owner ka PAN, address proof, bank account proof, GST certificate (agar GST-registered hain), aur authorised signatory ka ID.\n\nRe-verification 24 months ke baad ya details change hone par trigger ho sakti hai. Genuine re-verification requests sirf Dashboard ya @example.com emails se aati hain.\n\nDhanyavaad,\nRazorpay Support"),
    ("T-0070", "Hi Madhur Enterprises team,\n\nThanks for reaching out. Account closure is processed only after all pending settlements, refunds and disputes are cleared, so it can't be done right away. Our records show 1 refund in process and 1 open dispute on your account. I've passed your request to your Account Manager and the team that handles closures, and they will guide you through the next steps.\n\nRegards,\nRazorpay Support"),
    ("T-0104", "Namaste Jade Naturals team,\n\nHumse sampark karne ke liye dhanyavaad. Abhi aapke account par 0 open chargebacks hain. Disputes ko pehle se block nahi kiya ja sakta.\n\nJab koi customer kisi payment ko dispute karta hai, toh aapko Dashboard ke Disputes section mein notice milta hai. Aap evidence ke saath uska jawab de sakte hain. Iske liye aapko 7 calendar days ke andar evidence (invoice, proof of delivery aur customer ke saath communication) upload karna hoga. Deadline miss hone par dispute automatically lost ho jata hai.\n\nDhanyavaad,\nRazorpay Support"),
    ("T-0141", "Namaste Mosaic Crafts team,\n\nHumse sampark karne ke liye dhanyavaad. Webhook receive na hone par, kripya yeh check karein:\n1. Aapka webhook URL publicly reachable hai.\n2. Woh 5 seconds ke andar HTTP 200 return karta hai.\n3. Event aapki webhook settings mein subscribed hai.\n\nFailed webhooks 24 hours tak retry hote hain.\n\nDhanyavaad,\nRazorpay Support"),
]


def _sources(row):
    kb = " ".join(KB["articles"][k]["guidance"] for k in row["relevant_kb_ids"])
    return kb + " " + " ".join(f"{k} {v}" for k, v in row["merchant_facts_used"].items())


def _decision(row):
    from _lib.engine import Decision
    return Decision(row["category"], row["action"], row["escalation_team"], row["risk_level"], "x", row["relevant_kb_ids"])


@pytest.mark.parametrize("tid,draft", SAFE_CLAUDE_DRAFTS)
def test_safe_claude_drafts_now_pass(results, tid, draft):
    row = results[tid]["row"]
    t = BY_ID[tid]
    v = validate_reply(draft, row["drafted_reply"], _decision(row), t, MERCHANTS.get(t["merchant_id"]), NAMES,
                       row["language_style"], _sources(row))
    assert not v, [(x.rule, x.statement) for x in v]


@pytest.mark.parametrize("tid,draft,rule", [
    ("T-0015", "Hi,\n\nWe have escalated your refund; it will be credited within 5-7 business days. Regards", "escalation_timeline"),
    ("T-0016", "Hi,\n\nYour hold can last up to 5 business days and our specialist team will review it. Regards", "escalation_timeline"),
    ("T-0107", "Hi,\n\nWe will not delay further, it will reach you today. Regards", "time_promise"),
    ("T-0039", "Hi,\n\nYour settlement will arrive on 05 Oct 2026. Regards", "invented"),
])
def test_unsafe_drafts_still_blocked_with_kb_sources(results, tid, draft, rule):
    row = results[tid]["row"]
    t = BY_ID[tid]
    v = validate_reply(draft, row["drafted_reply"], _decision(row), t, MERCHANTS.get(t["merchant_id"]), NAMES,
                       row["language_style"], _sources(row))
    assert any(rule in x.rule for x in v), [x.rule for x in v]
