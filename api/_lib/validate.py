"""Output validation. A draft is only accepted if every check passes.

Core idea for invented facts: every number in a draft must already appear in the approved
base draft (built from the KB and the merchant record) or in the ticket itself. So a new
date, amount or timeline written by the model is always caught.
"""
import re
from dataclasses import dataclass

from .text import hinglish_hits, MERCHANT_ID_RE, PAYMENT_ID_RE, EMAIL_RE, PHONE_RE

MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


@dataclass
class Violation:
    rule: str
    kb_id: str | None
    statement: str
    reason: str


def _numbers(text: str) -> set:
    vals = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        vals.add(m.replace(",", "").rstrip(".").lstrip("0") or "0")
    return vals


def _snippet(text, m, width=60):
    s = max(0, m.start() - width // 2)
    return text[s:m.end() + width // 2].replace("\n", " ").strip()


PROMISE_PATTERNS = [
    (r"\b(we|i) (will|'ll|shall) (refund|release|waive|reduce|credit|reverse it)", "KB-16", "Promises a refund, release, waiver or credit"),
    (r"\b(will|shall) be (refunded|released|waived|credited) (today|tomorrow|by|on|within \d+ (hours?|minutes?))", "KB-16", "Promises a dated refund, release or credit"),
    (r"\b(has|have) been (approved|released|waived|refunded)\b", "KB-16", "Claims an approval or release that the system does not show"),
    (r"\b(approved|granted) (your|the) (waiver|refund|request|release)", "KB-09", "Claims approval of a waiver or refund"),
    (r"\bwaiver (is|has been) (approved|granted)", "KB-09", "Claims a fee waiver"),
    (r"\b(i|we) (promise|guarantee|assure you)\b", "KB-16", "Gives a guarantee"),
    (r"\bguaranteed\b", "KB-16", "Gives a guarantee"),
    (r"\b(rate|mdr|pricing) (has been|is now|will be) (reduced|lowered|changed|matched)", "KB-09", "Commits to a pricing change"),
    (r"\b(release|refund|waive|credit) kar (denge|diya|dete)\b", "KB-16", "Promises a refund, release or waiver (Hinglish)"),
    (r"\bpakka\b", "KB-16", "Gives a guarantee (Hinglish)"),
    (r"\byour refund (is|has been) (processed|completed|credited|on its way)", "KB-07", "Claims an individual refund status"),
    (r"\b(can|will) (close|deactivate) (your|the) account (now|immediately|today)", "KB-17", "Promises immediate closure"),
    (r"\b(block|stop|prevent) (the |this )?(chargeback|dispute) (for you|in advance)\b(?<!cannot)", "KB-08", "Claims a chargeback can be blocked"),
]
TIME_PROMISES = [r"\btoday\b", r"\btomorrow\b", r"\btonight\b", r"\bend of (the )?day\b", r"\bEOD\b", r"\baaj\b",
                 r"\bwithin the hour\b", r"\bright away\b", r"\bASAP\b"]
INTERNAL_RISK = [r"\brisk ops\b", r"\brisk team\b", r"\brisk review\b", r"\bchargeback reserve\b", r"\breserve\b",
                 r"\bfraud (check|team|flag)", r"\bflagged\b", r"\bsuspicious\b", r"\bunder investigation\b",
                 r"\binternal (reason|review|policy|check)", r"\bmonitoring\b", r"\bAML\b", r"\bwatchlist\b"]
FAULT_ADMISSION = [r"\bour (fault|mistake|error)\b", r"\bwe (made|have made) (a|an) (mistake|error)\b",
                   r"\bwe are (responsible|to blame)\b", r"\b(humari|hamari) galti\b", r"\bwe leaked\b",
                   r"\bapolog(ise|ize) for (the|this|our) (breach|leak|freeze|error)\b", r"\bdue to (a|an|our) (error|mistake|bug) on our (side|end)\b"]
ARGUMENT = [r"\byou are wrong\b", r"\bthat('s| is) not true\b", r"\byou should have\b", r"\byour (own )?fault\b",
            r"\baapki galti\b", r"\bcalm down\b", r"\bbaseless\b", r"\bno basis\b", r"\bas (you were|we) already (told|said)\b",
            r"\bthreats? (will not|won't) (help|work)\b"]
BLAME = [r"\b(customer|customers|their)('s)? (fault|mistake|error)\b", r"\bblame\b", r"\bcustomers? (are|is) (doing|entering) (it )?wrong"]
SECRET_REQUEST = r"(share|send|provide|give|email|paste|bhej\w*).{0,25}(key secret|secret key|api secret|password|\bOTP\b|api key)"
NEGATIONS = r"(never|not|don't|do not|na karein|mat|no one)"
RESOLVED_CLAIMS = [r"\b(has been|is now|is) (resolved|fixed|sorted)\b", r"\bissue (is|has been) closed\b"]


def validate_reply(draft: str, base_draft: str, decision, ticket: dict, merchant: dict | None,
                   all_merchants: dict, lang: str) -> list:
    v = []
    text = draft or ""
    low = text.lower()
    action = decision.action
    critical = decision.risk_level == "Critical"

    if len(text.strip()) < 20:
        v.append(Violation("empty_or_too_short", "KB-16", text[:80], "Draft is empty or too short"))
        return v
    if len(text) > 1600:
        v.append(Violation("too_long", "KB-16", text[:80] + "...", "Draft is longer than 1600 characters; KB-16 requires brief replies"))

    # 1. Invented numbers: dates, amounts, timelines -----------------------------------
    allowed = _numbers(base_draft) | _numbers(ticket.get("merchant_message", "")) | _numbers(ticket.get("ticket_id", ""))
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        n = m.group().replace(",", "").rstrip(".").lstrip("0") or "0"
        if n not in allowed:
            v.append(Violation("invented_number", "KB-16", _snippet(text, m),
                               f"Number '{m.group()}' is not in the merchant record or KB-approved statements (invented date, amount or timeline)"))
    for m in re.finditer(rf"\b\d{{1,2}}(st|nd|rd|th)? {MONTHS}\b|\b{MONTHS} \d{{1,2}}\b", low):
        if m.group() not in base_draft.lower():
            v.append(Violation("invented_date", "KB-02", _snippet(text, m), "Date not taken from the merchant record"))
    for p in TIME_PROMISES:
        for m in re.finditer(p, text, re.IGNORECASE):
            v.append(Violation("time_promise", "KB-16", _snippet(text, m), "Commits to a time ('today', 'tomorrow' and similar) not shown in the system"))

    # 2. Promises ---------------------------------------------------------------------
    for p, kb, why in PROMISE_PATTERNS:
        for m in re.finditer(p, text, re.IGNORECASE):
            v.append(Violation("promise", kb, _snippet(text, m), why))

    # 3. Internal risk disclosure ---------------------------------------------------------
    for p in INTERNAL_RISK:
        for m in re.finditer(p, text, re.IGNORECASE):
            v.append(Violation("internal_risk_disclosure", "KB-03", _snippet(text, m), "Reveals internal risk information or team names"))

    # 4. Legal / security cases: admissions and arguments ------------------------------------
    for p in FAULT_ADMISSION:
        for m in re.finditer(p, text, re.IGNORECASE):
            v.append(Violation("fault_admission", "KB-15", _snippet(text, m), "Admits fault"))
    for p in ARGUMENT:
        for m in re.finditer(p, text, re.IGNORECASE):
            v.append(Violation("argument", "KB-15", _snippet(text, m), "Argues with the merchant"))
    if critical:
        for p in RESOLVED_CLAIMS + [r"\bsteps to\b", r"\bplease (re-?add|update|retry|upload)\b"]:
            for m in re.finditer(p, text, re.IGNORECASE):
                v.append(Violation("critical_substantive_reply", "KB-15", _snippet(text, m),
                                   "Critical case received a substantive resolution instead of a safe acknowledgment"))

    # 5. Payment-failure specifics (KB-06) -----------------------------------------------
    if decision.category == "Payment Failure":
        for p in BLAME:
            for m in re.finditer(p, text, re.IGNORECASE):
                v.append(Violation("customer_blame", "KB-06", _snippet(text, m), "Blames the customer"))
        if re.search(r"\brefund", text, re.IGNORECASE):
            m = re.search(r"\brefund\w*", text, re.IGNORECASE)
            v.append(Violation("reversal_called_refund", "KB-06", _snippet(text, m),
                               "Calls a failed-payment auto-reversal a refund"))

    # 6. Secret keys --------------------------------------------------------------------------
    for m in re.finditer(SECRET_REQUEST, text, re.IGNORECASE):
        before = text[max(0, m.start() - 25):m.start()]
        if not re.search(NEGATIONS, before + m.group(), re.IGNORECASE):
            v.append(Violation("secret_request", "KB-14", _snippet(text, m), "Asks for a secret key, password or OTP"))

    # 7. Unrelated merchant data and PII ---------------------------------------------------------
    own = ticket.get("merchant_id")
    for m in MERCHANT_ID_RE.finditer(text):
        if m.group() != own:
            v.append(Violation("other_merchant_data", "KB-16", _snippet(text, m), "Mentions another merchant's ID"))
    own_name = (merchant or {}).get("business_name", "")
    for mid, name in all_merchants.items():
        if name and name != own_name and name.lower() in low:
            v.append(Violation("other_merchant_data", "KB-16", name, f"Mentions another merchant ({name})"))
    ticket_pids = set(PAYMENT_ID_RE.findall(ticket.get("merchant_message", "")))
    for m in PAYMENT_ID_RE.finditer(text):
        if m.group() not in ticket_pids:
            v.append(Violation("other_payment_data", "KB-16", m.group(), "Mentions a payment ID not in this ticket"))
    for m in EMAIL_RE.finditer(text):
        if not m.group().lower().endswith("example.com"):
            v.append(Violation("pii_leak", "KB-16", m.group(), "Contains an email address"))
    for m in PHONE_RE.finditer(text):
        v.append(Violation("pii_leak", "KB-16", m.group(), "Contains a phone number"))

    # 8. Language style ----------------------------------------------------------------------
    hits = hinglish_hits(text)
    if lang == "Hinglish" and hits < 3:
        v.append(Violation("language_mismatch", "KB-16", text[:80], "Merchant wrote in Hinglish but the draft is not in Hinglish"))
    if lang == "English" and hits > 2:
        v.append(Violation("language_mismatch", "KB-16", text[:80], "Merchant wrote in English but the draft uses Hinglish"))

    # 9. Action consistency ---------------------------------------------------------------------
    if action == "ESCALATE":
        if not re.search(r"escalat|team|specialist|account manager", low):
            v.append(Violation("action_mismatch", "KB-15", text[:80], "Escalated ticket but the draft does not say it was passed to a team"))
        for p in RESOLVED_CLAIMS:
            for m in re.finditer(p, text, re.IGNORECASE):
                v.append(Violation("action_mismatch", "KB-16", _snippet(text, m), "Escalated ticket but the draft claims it is resolved"))
    if action == "ASK_FOR_INFO" and not re.search(r"\?|please share|kripya .{0,60}share|share karein|confirm", low):
        v.append(Violation("action_mismatch", "KB-16", text[:80], "Ask-for-info ticket but the draft does not ask for anything"))

    # De-duplicate
    seen, out = set(), []
    for x in v:
        key = (x.rule, x.statement)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out
