"""Deterministic keyword checks.

1. detect_critical_signals(): always runs. Claude can add signals but never remove these.
2. classify_by_keywords(): fallback classifier used when Claude is unavailable or invalid.
"""
import re

CRITICAL_PATTERNS = {
    "legal": [
        r"legal notice", r"\blawyer", r"\badvocate\b", r"\bcourt\b", r"consumer forum", r"\bpolice\b", r"\bFIR\b",
        r"\bsue\b", r"which law", r"\blegal action\b", r"fraud kar rahe", r"you (are|people are) (frauds?|cheating|scamm)",
        r"\bscam(mers)?\b", r"\bcheaters?\b",
    ],
    "regulator": [r"\bRBI\b", r"\bregulator", r"\bNPCI\b", r"\bSEBI\b", r"ombudsman", r"reporting to the authorities",
                  r"complain(ed|t)? to (the )?(rbi|regulator|government|authorities)"],
    "media": [r"\btwitter\b", r"\blinkedin\b", r"\bthe press\b", r"\bmedia\b", r"social media", r"\bnews ?paper", r"go viral",
              r"\bpost (this|about this)\b", r"tagging your ceo", r"\byoutube\b"],
    "data_security": [r"data (leak|breach)", r"\bleak(ed)?\b", r"\bbreach\b", r"card details (were|are) visible",
                      r"data exposed", r"\bhack(ed)?\b", r"data ka leak"],
    "account_takeover": [r"logged into my (dashboard|account)", r"changed my bank account", r"i did not do this",
                         r"unauthori[sz]ed (login|access|change)", r"someone (has )?(logged|changed|accessed)",
                         r"account (hijack|takeover)"],
    "staff_misconduct": [r"(asked|demanded) (me )?for (a )?(commission|bribe|money|cut)", r"\bbrib(e|ery)\b",
                         r"\bkickback\b", r"employee.{0,40}(commission|bribe)", r"staff.{0,40}(commission|bribe)"],
}


def detect_critical_signals(message: str) -> list:
    found = []
    for signal, patterns in CRITICAL_PATTERNS.items():
        if any(re.search(p, message, re.IGNORECASE) for p in patterns):
            found.append(signal)
    return found


# Ordered: first match wins. Critical categories are handled before this list.
CATEGORY_PATTERNS = [
    ("Account Closure", [r"close (my|the) account", r"account (band|close) kar"]),
    ("Chargeback", [r"chargeback", r"\bdisput(e|ed)\b"]),
    ("Fees & Pricing", [r"refund the difference", r"\bwaiver?\b", r"\bMDR\b", r"rate kam", r"reduce my rates?",
                        r"\binvoice\b", r"charged \d", r"\bpricing\b", r"\bfee(s)?\b"]),
    ("Refund", [r"\brefund"]),
    ("KYC", [r"\bKYC\b", r"re-?verification"]),
    ("Account Change", [r"(email|mobile|phone).{0,30}(change|update)", r"change.{0,30}(email|mobile)",
                        r"naam change", r"name change", r"business type"]),
    ("API & Integration", [r"SIGNATURE_MISMATCH", r"signature verification", r"BAD_REQUEST_ERROR", r"authentication failed",
                           r"webhook", r"rate-?limit", r"\b5\d\d errors?\b", r"\bAPI\b", r"popup", r"times? out",
                           r"\bSDK\b", r"integration"]),
    ("Payment Failure", [r"payment.{0,20}fail", r"fail.{0,20}payment", r"payments? (are )?failing", r"payment success",
                         r"money was deducted", r"deducted but", r"payment problem", r"payment failed",
                         r"payments? fail ho"]),
    ("Settlement", [r"settlement", r"payout", r"\bpaisa\b", r"money not received", r"credit nahi", r"\bhold\b"]),
]


def classify_by_keywords(message: str, critical_signals: list) -> str:
    from .taxonomy import SIGNAL_TO_CATEGORY, CRITICAL_PRECEDENCE
    if critical_signals:
        cats = {SIGNAL_TO_CATEGORY[s] for s in critical_signals}
        for c in CRITICAL_PRECEDENCE:
            if c in cats:
                return c
    for category, patterns in CATEGORY_PATTERNS:
        if any(re.search(p, message, re.IGNORECASE) for p in patterns):
            return category
    return "Unclear"


def secondary_topic(message: str) -> str | None:
    """Underlying topic for critical tickets (for example a legal threat about payouts)."""
    for category, patterns in CATEGORY_PATTERNS:
        if any(re.search(p, message, re.IGNORECASE) for p in patterns):
            return category
    return None
