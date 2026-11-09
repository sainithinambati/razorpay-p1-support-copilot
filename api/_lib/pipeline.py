"""One ticket in, one auditable result out. No database access here (see db.py)."""
from . import claude
from .engine import decide
from .keywords import detect_critical_signals, classify_by_keywords, secondary_topic
from .replies import build_reply, fallback_ack
from .text import extract, mask_pii, detect_language, detect_tone, validate_ticket
from .validate import validate_reply

PERMITTED_FACT_KEYS = {
    "settlement_status", "last_settlement_date", "last_settlement_amount_inr", "expected_settlement_date",
    "payment_success_rate_7d_pct", "open_chargebacks", "refunds_in_process", "kyc_status",
    "gst_invoice_available", "payment_id", "refund_amount_inr",
}

PROHIBITED = [
    "Promising any date, refund, waiver, settlement release, pricing change or closure not shown in the system",
    "Inventing numbers, dates, amounts or timelines",
    "Revealing internal risk reasons, hold reasons or internal team names",
    "Admitting fault, arguing, or blaming the merchant or their customers",
    "Asking for passwords, OTPs or secret keys",
    "Mentioning any other merchant or payment",
]


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if hasattr(v, "is_integer") or type(v).__name__ == "Decimal":
            v = float(v)
            v = int(v) if v.is_integer() else v
        out[k] = v
    return out


def analyse(ticket: dict, merchant: dict | None, kb: dict, all_merchants: dict, use_claude: bool, events: list) -> dict:
    """kb = {'rules_version', 'parameters', 'team_mapping', 'articles'}. Returns the analysis row + side outputs."""
    def log(stage, message, level="info", **details):
        events.append({"stage": stage, "level": level, "message": message, "details": details})

    msg = ticket["merchant_message"]
    params, team_map = kb["parameters"], kb["team_mapping"]

    # Prepare input -----------------------------------------------------------------------
    problems = validate_ticket(ticket)
    if problems:
        log("validate_input", "Input problems found", "warning", problems=problems)
    ext = extract(msg)
    masked = mask_pii(msg)
    lang = detect_language(msg)
    tone = detect_tone(msg)
    py_signals = detect_critical_signals(msg)
    kw_category = classify_by_keywords(msg, py_signals)
    log("prepare_input", "Input prepared", language=lang, tone=tone, python_signals=py_signals,
        extracted={k: ext[k] for k in ("payment_id", "amount_inr", "days_elapsed", "error_codes", "message_merchant_ids")})

    # Classify ---------------------------------------------------------------------------
    cls = None
    if use_claude and claude.enabled():
        try:
            cls = claude.classify(masked, ticket.get("channel", ""))
            log("classify", "Claude classification accepted", category=cls["category"], attempts=cls.get("attempts"),
                confidence=cls.get("confidence"), signals=cls.get("risk_signals"))
        except claude.ClaudeError as e:
            cls = {"category": kw_category, "risk_signals": [], "confidence": None, "source": "keyword_fallback",
                   "fallback_reason": str(e)[:300]}
            log("classify", "Claude classification failed; keyword fallback used", "warning", error=str(e)[:300])
    if cls is None:
        cls = {"category": kw_category, "risk_signals": [], "confidence": None, "source": "keyword_fallback",
               "secondary_topic": secondary_topic(msg) if kw_category in ("Legal / Regulatory", "Security", "Staff Misconduct") else None}
        log("classify", "Keyword classifier used (Claude not configured)")
    # Claude's language/tone are advisory; deterministic detection is authoritative for validation.
    if cls.get("source") == "claude" and cls.get("language_style") in ("English", "Hinglish") and cls["language_style"] != lang:
        log("classify", "Language disagreement; deterministic detection kept", "info", claude=cls["language_style"], python=lang)

    # Decide (deterministic) -----------------------------------------------------------------
    d = decide(ticket, merchant, cls, ext, params, team_map, kw_category, py_signals)
    log("decide", f"{d.action} / {d.escalation_team or '-'} / {d.risk_level}", trace=d.trace)

    # Draft ------------------------------------------------------------------------------
    name = (merchant or {}).get("business_name")
    base = build_reply(d, lang, tone, params, ext, name)
    facts = _jsonable({k: v for k, v in d.facts.items() if k in PERMITTED_FACT_KEYS and v is not None})
    fact_sheet = {
        "ticket_category": d.category, "approved_action": d.action, "risk_level": d.risk_level,
        "approved_escalation_team": d.escalation_team, "merchant_facts_permitted": facts,
        "relevant_kb_ids": d.kb_ids, "language_style_required": lang, "tone": tone,
        "ask_for": d.ask_for, "prohibited": PROHIBITED,
        "kb_text": {k: kb["articles"].get(k, {}).get("guidance") for k in d.kb_ids},
    }
    draft, model_used, draft_source = base, None, "template"
    if use_claude and claude.enabled():
        try:
            draft = claude.polish(fact_sheet, base)
            model_used, draft_source = claude.model_name(), "claude"
            log("draft", "Claude draft received")
        except claude.ClaudeError as e:
            log("draft", "Claude drafting failed; approved template used", "warning", error=str(e)[:300])
    else:
        log("draft", "Approved template used (Claude not configured)")

    # Validate ---------------------------------------------------------------------------
    blocked = None
    approved_sources = " ".join(str(x) for x in fact_sheet["kb_text"].values() if x) + " " + \
        " ".join(f"{k} {v}" for k, v in facts.items())
    violations = validate_reply(draft, base, d, ticket, merchant, all_merchants, lang, approved_sources)
    validation_status, failure_reason = "passed", None
    if violations:
        blocked = {
            "failed_draft": draft,
            "unsafe_statement": violations[0].statement[:300],
            "reason": "; ".join(sorted({x.reason for x in violations}))[:900],
            "violated_kb_id": violations[0].kb_id,
            "fallback_action": d.action,
            "required_team": d.escalation_team or "Support Triage",
            "rules": [x.rule for x in violations],
        }
        log("validate_output", f"Draft blocked: {len(violations)} violation(s)", "warning",
            violations=[x.__dict__ for x in violations][:10])
        validation_status = "failed_fallback"
        failure_reason = f"Draft blocked ({', '.join(sorted({x.rule for x in violations}))})"
        d.human_review_required = True
        d.review_reasons.append("Draft failed output validation")
        # Safe fallback: the approved template if it passes, otherwise a generic acknowledgment.
        if draft_source == "claude" and not validate_reply(base, base, d, ticket, merchant, all_merchants, lang, approved_sources):
            draft = base
        else:
            draft = fallback_ack(lang, d.action)
        draft_source = "fallback"
    else:
        log("validate_output", "Draft passed all checks")

    return {
        "row": {
            "ticket_id": ticket["ticket_id"],
            "merchant_id": ticket.get("merchant_id"),
            "category": d.category,
            "action": d.action,
            "escalation_team": d.escalation_team,
            "risk_level": d.risk_level,
            "drafted_reply": draft,
            "secondary_topic": d.secondary_topic,
            "risk_signals": d.risk_signals,
            "language_style": lang,
            "tone": tone,
            "confidence": cls.get("confidence"),
            "classifier_source": cls.get("source"),
            "payment_id": ext.get("payment_id"),
            "refund_amount_inr": ext.get("amount_inr") if d.category == "Refund" else None,
            "relevant_kb_ids": d.kb_ids,
            "merchant_facts_used": facts,
            "decision_trace": d.trace + ([f"Review: {r}" for r in d.review_reasons]),
            "escalation_team_primary": d.team_primary,
            "escalation_team_secondary": d.team_secondary,
            "validation_status": validation_status,
            "human_review_required": d.human_review_required,
            "failure_reason": failure_reason or ("; ".join(d.review_reasons) if d.review_reasons else None),
            "model_name": model_used or "deterministic-template",
            "rules_version": kb["rules_version"],
        },
        "blocked": blocked,
        "kb_suggestion": d.kb_suggestion,
        "draft_source": draft_source,
        "keyword_category": kw_category,
    }


def failed_row(ticket: dict, kb: dict, error: str) -> dict:
    """Mandatory row when processing fails after all retries."""
    msg = ticket.get("merchant_message") or ""
    sig = detect_critical_signals(msg)
    cat = classify_by_keywords(msg, sig)
    lang = detect_language(msg)
    critical = bool(sig)
    action = "ESCALATE" if critical else "ASK_FOR_INFO"
    team = None
    if critical:
        from .taxonomy import CATEGORY_TO_ROUTE
        team = kb["team_mapping"][CATEGORY_TO_ROUTE[cat]]
    return {
        "ticket_id": ticket["ticket_id"], "merchant_id": ticket.get("merchant_id"), "category": cat,
        "action": action, "escalation_team": team, "risk_level": "Critical" if critical else "Medium",
        "drafted_reply": fallback_ack(lang, action), "risk_signals": sig, "language_style": lang,
        "classifier_source": "keyword_fallback", "relevant_kb_ids": ["KB-16"], "merchant_facts_used": {},
        "decision_trace": [f"Processing failed after retries: {error[:300]}"],
        "validation_status": "processing_failed", "human_review_required": True,
        "failure_reason": f"Processing failed: {error[:300]}", "model_name": None,
        "rules_version": kb["rules_version"],
    }
