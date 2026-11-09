"""Fixed vocabularies. Nothing outside these lists is ever accepted."""

CATEGORIES = [
    "Settlement",
    "Payment Failure",
    "Refund",
    "Chargeback",
    "Fees & Pricing",
    "KYC",
    "Account Change",
    "Account Closure",
    "API & Integration",
    "Legal / Regulatory",
    "Security",
    "Staff Misconduct",
    "Unclear",
]

CRITICAL_CATEGORIES = {"Legal / Regulatory", "Security", "Staff Misconduct"}

ACTIONS = ["AUTO_RESOLVE", "ASK_FOR_INFO", "ESCALATE"]

RISK_LEVELS = ["Critical", "High", "Medium", "Low"]

TEAMS = [
    "Legal",
    "Security",
    "Risk Ops",
    "Payments Ops",
    "Refund Ops",
    "Account Manager",
    "Account Manager + Risk Ops",
    "Tech Support",
]

RISK_SIGNALS = ["legal", "regulator", "media", "data_security", "account_takeover", "staff_misconduct"]

KB_IDS = [f"KB-{i:02d}" for i in range(1, 18)]

LANGUAGES = ["English", "Hinglish"]
TONES = ["neutral", "frustrated", "angry"]

# Category precedence when several critical signals are present.
SIGNAL_TO_CATEGORY = {
    "data_security": "Security",
    "account_takeover": "Security",
    "staff_misconduct": "Staff Misconduct",
    "legal": "Legal / Regulatory",
    "regulator": "Legal / Regulatory",
    "media": "Legal / Regulatory",
}
CRITICAL_PRECEDENCE = ["Security", "Staff Misconduct", "Legal / Regulatory"]

# Routing keys used in kb_rule_versions.team_mapping
CATEGORY_TO_ROUTE = {
    "Legal / Regulatory": "legal",
    "Security": "security",
    "Staff Misconduct": "staff_misconduct",
}

AUTO_SEND_ENABLED = False  # Hard-coded. Not configurable by env or KB version.
