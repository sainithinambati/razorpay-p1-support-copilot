"""Deterministic policy engine. Python owns action, team, risk level and KB selection.

Priority order (prompt Section 10):
  1. validate input  2. critical-risk gate  3. identity  4. mandatory escalations
  5. merchant status / thresholds  6. missing information  7. auto-resolve eligibility
Claude's output can only supply the category (from the fixed list) and extra critical signals.
"""
import re
from dataclasses import dataclass, field

from .taxonomy import (CATEGORIES, CRITICAL_CATEGORIES, CRITICAL_PRECEDENCE, SIGNAL_TO_CATEGORY,
                       CATEGORY_TO_ROUTE)
from .text import content_word_count, validate_ticket

VAGUE_WORD_LIMIT = 4


@dataclass
class Decision:
    category: str
    action: str
    escalation_team: str | None
    risk_level: str
    template: str
    kb_ids: list
    facts: dict = field(default_factory=dict)
    ask_for: list = field(default_factory=list)
    risk_signals: list = field(default_factory=list)
    secondary_topic: str | None = None
    human_review_required: bool = False
    review_reasons: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    variant: dict = field(default_factory=dict)
    kb_suggestion: dict | None = None
    team_primary: str | None = None
    team_secondary: str | None = None

    def escalate(self, team):
        self.action = "ESCALATE"
        self.escalation_team = team
        if team and "+" in team:
            self.team_primary, self.team_secondary = [t.strip() for t in team.split("+", 1)]
        else:
            self.team_primary, self.team_secondary = team, None


def _merchant_facts(merchant: dict, keys: list) -> dict:
    return {k: merchant.get(k) for k in keys if merchant.get(k) is not None}


def _num(v):
    return None if v is None else float(v)


def decide(ticket: dict, merchant: dict | None, cls: dict, ext: dict, params: dict, team_map: dict,
           keyword_category: str, python_signals: list) -> Decision:
    msg = ticket["merchant_message"]
    low = msg.lower()
    trace = []

    # 1. Input validation -------------------------------------------------------------
    problems = validate_ticket(ticket)
    if any("merchant_message" in p for p in problems):
        d = Decision("Unclear", "ASK_FOR_INFO", None, "Medium", "ask_details", ["KB-16"],
                     ask_for=["details"], trace=[f"Input invalid: {problems}"])
        d.human_review_required = True
        d.review_reasons.append("Invalid input")
        return d

    # Category from the classifier, constrained to the fixed taxonomy.
    category = cls.get("category") if cls.get("category") in CATEGORIES else keyword_category
    trace.append(f"Classifier category: {cls.get('category')} (source: {cls.get('source')}); keyword category: {keyword_category}")

    # 2. Critical-risk gate (runs before identity, so a missing ID never hides a threat) ---
    claude_signals = [s for s in cls.get("risk_signals", []) if s in SIGNAL_TO_CATEGORY]
    signals = sorted(set(python_signals) | set(claude_signals))
    if not signals and category in CRITICAL_CATEGORIES:
        # Claude says critical but no explicit signal: still treat as critical (fail safe).
        signals = {"Legal / Regulatory": ["legal"], "Security": ["data_security"],
                   "Staff Misconduct": ["staff_misconduct"]}[category]
        trace.append("Classifier chose a critical category; treated as critical (fail safe)")
    if signals:
        cats = {SIGNAL_TO_CATEGORY[s] for s in signals}
        crit_cat = next(c for c in CRITICAL_PRECEDENCE if c in cats)
        secondary = cls.get("secondary_topic") if cls.get("secondary_topic") in CATEGORIES else None
        if category not in CRITICAL_CATEGORIES and category != "Unclear":
            secondary = secondary or category
        d = Decision(crit_cat, "ESCALATE", None, "Critical", "critical_ack", ["KB-15", "KB-16"],
                     risk_signals=signals, secondary_topic=secondary, trace=trace)
        d.escalate(team_map[CATEGORY_TO_ROUTE[crit_cat]])
        d.variant = {"security": crit_cat == "Security"}
        d.human_review_required = True
        d.review_reasons.append("Critical case: human team owns the response")
        trace.append(f"Critical signals {signals} -> {crit_cat}, escalate to {d.escalation_team} (KB-15)")
        return d

    # 3. Identity ------------------------------------------------------------------------
    mid = ticket.get("merchant_id")
    msg_ids = ext.get("message_merchant_ids", [])
    if mid and msg_ids and any(x != mid for x in msg_ids):
        d = Decision(category, "ESCALATE", None, "Critical", "identity_conflict", ["KB-15", "KB-16"],
                     risk_signals=["identity_conflict"], trace=trace)
        d.escalate(team_map["identity_conflict"])
        d.human_review_required = True
        d.review_reasons.append("Identity anomaly: structured ID and message ID differ")
        trace.append(f"Identity conflict: structured {mid} vs message {msg_ids}")
        return d
    if not mid or merchant is None:
        ask = ["merchant_id"]
        if category == "Refund" and not ext.get("payment_id"):
            ask.append("payment_id")
        d = Decision(category, "ASK_FOR_INFO", None, "Medium", "ask_merchant_id", ["KB-16"], ask_for=ask, trace=trace)
        trace.append("Structured merchant_id missing or unknown -> ASK_FOR_INFO (KB-16); no ID inferred from text")
        return _finish(d, cls, keyword_category, params)

    # 4-7. Category rules ------------------------------------------------------------------
    vague = content_word_count(msg) <= VAGUE_WORD_LIMIT
    sr = _num(merchant.get("payment_success_rate_7d_pct"))
    threshold = float(params["payment_success_threshold_pct"])
    status = merchant.get("settlement_status")
    hold = merchant.get("hold_reason")

    if category == "Settlement":
        base_facts = _merchant_facts(merchant, ["settlement_status", "last_settlement_date", "last_settlement_amount_inr"])
        if hold in ("Risk review", "Chargeback reserve"):
            d = Decision(category, "ESCALATE", None, "High", "settlement_hold_escalate", ["KB-03", "KB-16"],
                         facts={"settlement_status": status}, trace=trace)
            d.escalate(team_map["settlement_hold"])
            trace.append("Settlement hold type requires Risk Ops (KB-03); hold reason kept internal")
        elif status == "Delayed" and merchant.get("expected_settlement_date"):
            d = Decision(category, "AUTO_RESOLVE", None, "Medium", "settlement_delayed", ["KB-02", "KB-01"],
                         facts={**base_facts, "expected_settlement_date": merchant["expected_settlement_date"]}, trace=trace)
            trace.append("Delayed with a system date -> share only expected_settlement_date (KB-02)")
        elif hold == "KYC pending":
            d = Decision(category, "AUTO_RESOLVE", None, "Medium", "settlement_kyc_hold", ["KB-04", "KB-10"],
                         facts={"settlement_status": status, "kyc_status": merchant.get("kyc_status")}, trace=trace)
            trace.append("KYC-pending hold -> guide to Dashboard > Account > KYC (KB-04)")
        elif hold == "Bank account mismatch":
            d = Decision(category, "AUTO_RESOLVE", None, "Medium", "settlement_bank_mismatch", ["KB-05"],
                         facts={"settlement_status": status}, trace=trace)
            trace.append("Bank account mismatch hold -> re-add account, penny-drop (KB-05)")
        elif status == "Delayed":
            d = Decision(category, "ESCALATE", None, "Medium", "generic_escalate", ["KB-02", "KB-16"],
                         facts={"settlement_status": status}, trace=trace)
            d.escalate(team_map["settlement_hold"])
            trace.append("Delayed but no system date: cannot share a date, escalate")
        elif vague:
            d = Decision(category, "ASK_FOR_INFO", None, "Medium", "ask_details", ["KB-01", "KB-16"],
                         ask_for=["settlement_details"], trace=trace)
            trace.append("Vague settlement message and record shows nothing pending -> ask for details")
        else:
            d = Decision(category, "AUTO_RESOLVE", None, "Medium", "settlement_settled", ["KB-01"],
                         facts=base_facts, trace=trace)
            trace.append("Status Settled -> T+2 guidance with last settlement from the record (KB-01)")

    elif category == "Payment Failure":
        deducted = bool(re.search(r"deduct|debited", low))
        facts = {"payment_success_rate_7d_pct": sr}
        if sr is not None and sr < threshold:
            d = Decision(category, "ESCALATE", None, "High", "payment_degradation", ["KB-06"], facts=facts, trace=trace)
            d.escalate(team_map["payment_degradation"])
            trace.append(f"7-day success {sr}% < {threshold}% -> possible degradation, Payments Ops (KB-06)")
        elif vague:
            d = Decision(category, "ASK_FOR_INFO", None, "Medium", "ask_payment_details", ["KB-06", "KB-16"],
                         facts=facts, ask_for=["payment_details"], trace=trace)
            trace.append("Vague payment-failure message, success rate normal -> ask for payment details")
        else:
            d = Decision(category, "AUTO_RESOLVE", None, "Medium", "payment_failure", ["KB-06"], facts=facts, trace=trace)
            trace.append(f"7-day success {sr}% >= {threshold}% -> common causes and retry guidance (KB-06)")
        d.variant = {"deducted": deducted}

    elif category == "Refund":
        pid, amt, days = ext.get("payment_id"), ext.get("amount_inr"), ext.get("days_elapsed")
        if not pid:
            d = Decision(category, "ASK_FOR_INFO", None, "Medium", "ask_payment_id", ["KB-07", "KB-16"],
                         ask_for=["payment_id"], trace=trace)
            trace.append("Refund without payment ID -> ask for it first (KB-07, KB-16)")
        elif amt is None:
            d = Decision(category, "ASK_FOR_INFO", None, "Medium", "ask_refund_amount", ["KB-07", "KB-16"],
                         ask_for=["refund_amount"], trace=trace)
            trace.append("Refund amount unknown -> cannot apply the high-value rule, ask for it")
        elif amt >= float(params["high_value_refund_inr"]):
            d = Decision(category, "ESCALATE", None, "High", "refund_high_value", ["KB-07"], trace=trace)
            d.escalate(team_map["high_value_refund"])
            trace.append(f"Refund Rs {amt:,.0f} >= Rs {float(params['high_value_refund_inr']):,.0f} -> Refund Ops, no timeline (KB-07)")
        else:
            d = Decision(category, "AUTO_RESOLVE", None, "Medium", "refund_standard", ["KB-07"], trace=trace)
            trace.append("Standard refund -> 5-7 business days and ARN guidance, no individual status claimed (KB-07)")
            if days is not None and days > 7:
                d.human_review_required = True
                d.review_reasons.append(f"Merchant says the refund started {days} days ago (beyond the 5-7 business day window)")
                d.kb_suggestion = {"kb_id": "KB-07", "topic": "Overdue refunds",
                                   "suggestion": "KB-07 has no guidance for refunds still pending after 5-7 business days. Add an escalation path for overdue refunds."}
        d.facts = {"payment_id": pid, "refund_amount_inr": amt}

    elif category == "Chargeback":
        block = bool(re.search(r"\b(block|rok|stop|prevent)\b", low)) or "rok sakte" in low
        no_notice = "notice" in low and ("don't see" in low or "do not see" in low or "no notice" in low)
        d = Decision(category, "AUTO_RESOLVE", None, "Medium", "chargeback", ["KB-08"],
                     facts=_merchant_facts(merchant, ["open_chargebacks"]), trace=trace)
        d.variant = {"block": block, "no_notice": no_notice}
        trace.append("Chargeback -> 7-calendar-day evidence rule, approved evidence types (KB-08)")

    elif category == "Fees & Pricing":
        pricing = bool(re.search(r"waive|waiver|\bmdr\b|rate|reduce|difference|charged \d|negotiat|pricing|%", low))
        if pricing:
            d = Decision(category, "ESCALATE", None, "Medium", "pricing_escalate", ["KB-09"], trace=trace)
            d.escalate(team_map["pricing"])
            trace.append("Pricing, waiver or fee-difference request -> Account Manager only (KB-09)")
        else:
            d = Decision(category, "AUTO_RESOLVE", None, "Low", "invoice", ["KB-09"],
                         facts=_merchant_facts(merchant, ["gst_invoice_available"]), trace=trace)
            trace.append("Invoice request -> generated by the 5th under Dashboard > Reports > Invoices (KB-09)")

    elif category == "KYC":
        reverify = bool(re.search(r"re-?verif|genuine", low))
        d = Decision(category, "AUTO_RESOLVE", None, "Medium" if reverify else "Low",
                     "kyc_reverification" if reverify else "kyc_documents", ["KB-10"],
                     facts=_merchant_facts(merchant, ["kyc_status"]), trace=trace)
        trace.append("KYC -> approved document list / genuine re-verification channels (KB-10)")

    elif category == "Account Change":
        legal_change = bool(re.search(r"naam|name|business type|legal entity|company", low)) and not re.search(r"email|mobile", low)
        d = Decision(category, "AUTO_RESOLVE", None, "Low", "account_change", ["KB-11"], trace=trace)
        d.variant = {"legal_change": legal_change}
        trace.append("Account details change -> OTP for email/mobile, fresh application for entity/type (KB-11)")

    elif category == "Account Closure":
        facts = _merchant_facts(merchant, ["settlement_status", "refunds_in_process", "open_chargebacks"])
        d = Decision(category, "ESCALATE", None, "High", "closure", ["KB-17"], facts=facts, trace=trace)
        d.escalate(team_map["closure"])
        trace.append(f"Closure -> Account Manager + Risk Ops after obligations clear; checked {facts} (KB-17)")

    elif category == "API & Integration":
        if "signature" in low:
            d = Decision(category, "AUTO_RESOLVE", None, "Low", "api_signature", ["KB-12"], trace=trace)
        elif "amount mismatch" in low or "paise" in low:
            d = Decision(category, "AUTO_RESOLVE", None, "Low", "api_amount", ["KB-12"], trace=trace)
        elif "webhook" in low:
            d = Decision(category, "AUTO_RESOLVE", None, "Low", "webhook", ["KB-13"], trace=trace)
        elif "authentication failed" in low or "test key" in low or "live key" in low:
            d = Decision(category, "AUTO_RESOLVE", None, "Low", "test_live_keys", ["KB-14"], trace=trace)
        else:
            d = Decision(category, "ESCALATE", None, "Medium", "tech_escalate", ["KB-16"], trace=trace)
            d.escalate(team_map["tech_fault"])
            d.kb_suggestion = {"kb_id": None, "topic": "Uncovered technical issue",
                               "suggestion": f"No KB article covers this technical issue: \"{msg[:120]}\". Consider adding troubleshooting guidance."}
            trace.append("Technical fault with no KB fix -> Tech Support")
        if d.action == "AUTO_RESOLVE":
            trace.append(f"API issue with an approved KB fix ({d.kb_ids[0]})")

    else:  # Unclear
        d = Decision("Unclear", "ASK_FOR_INFO", None, "Medium", "ask_details", ["KB-16"], ask_for=["details"], trace=trace)
        trace.append("Topic cannot be identified -> ask for details (KB-16)")

    return _finish(d, cls, keyword_category, params)


def _finish(d: Decision, cls: dict, keyword_category: str, params: dict) -> Decision:
    conf = cls.get("confidence")
    if conf is not None and conf < float(params["confidence_review_threshold"]):
        d.human_review_required = True
        d.review_reasons.append(f"Low classifier confidence ({conf:.2f})")
    if cls.get("source") == "claude" and cls.get("category") != keyword_category:
        d.trace.append(f"Note: Claude ({cls.get('category')}) and keyword check ({keyword_category}) disagree")
    if cls.get("source") == "keyword_fallback" and cls.get("fallback_reason"):
        d.human_review_required = True
        d.review_reasons.append(f"Claude classification unavailable: {cls['fallback_reason']}")
    return d
