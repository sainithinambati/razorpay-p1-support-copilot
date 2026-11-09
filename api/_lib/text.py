"""Deterministic text processing: validation, extraction, masking, language and tone."""
import re

MAX_MESSAGE_LEN = 2000

PAYMENT_ID_RE = re.compile(r"\bpay_[A-Za-z0-9]{6,}\b")
MERCHANT_ID_RE = re.compile(r"\bM\d{4}\b")
AMOUNT_RE = re.compile(r"(?:Rs\.?|INR|₹)\s*([0-9][0-9,]*(?:\.\d+)?)", re.IGNORECASE)
DAYS_RE = re.compile(r"(\d+)\s*(?:days?|din)\b", re.IGNORECASE)
ERROR_CODE_RE = re.compile(r"\b(SIGNATURE_MISMATCH|BAD_REQUEST_ERROR|GATEWAY_ERROR|SERVER_ERROR)\b")

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
ACCOUNT_RE = re.compile(r"(?<!\d)\d{9,18}(?!\d)")

HINGLISH_WORDS = {
    "hai", "hain", "nahi", "nahin", "kya", "kab", "mera", "mere", "meri", "karo", "kaha", "kahan", "hua", "ho",
    "rahe", "raha", "rahi", "gaye", "gaya", "gayi", "aap", "aapki", "aapka", "bhai", "ka", "ki", "ke", "me",
    "mein", "se", "pe", "par", "kaise", "karein", "kare", "abhi", "tak", "kitna", "lagenge", "aaya", "milega",
    "dono", "ruk", "warna", "doosre", "chahiye", "rok", "sakte", "karega", "lag", "batao", "kuch", "din",
    "kyun", "kiya", "humne", "wajah", "jaa", "hu", "hoon", "dikh", "alawa", "tumhari", "tum", "paisa", "kaafi",
    "karunga", "baithe", "aur", "bhi", "toh", "yeh", "woh", "kaun", "kyunki", "liye", "wala", "karna",
    "chalejayenge", "chale", "jayenge", "mujhe", "upar", "humare", "hamare", "kijiye", "dijiye", "bataiye",
}

ANGRY_PATTERNS = [
    r"final warning", r"\bfraud\b", r"lawyer", r"legal notice", r"!!", r"\bURGENT\b", r"immediately",
    r"or i will move", r"\bwarna\b", r"do something", r"no one has told me", r"which law", r"consumer (court|forum)",
    r"if this is not fixed", r"\bpolice\b", r"reporting to the authorities", r"I did not do this", r"I have a recording",
]
FRUSTRATED_PATTERNS = [
    r"again and again", r"\bstuck\b", r"ruk (gaya|gayi)", r"kaafi din", r"need this today", r"abhi tak",
    r"urgent", r"since a few days", r"kab milega", r"\?\?", r"business ruk", r"sales ruk", r"kaha hai",
    r"has not reached", r"still pending", r"customer is asking", r"too high",
]


def validate_ticket(ticket: dict) -> list:
    """Return a list of structural problems (empty list = valid)."""
    problems = []
    if not re.fullmatch(r"T-\d{4}", str(ticket.get("ticket_id") or "")):
        problems.append("ticket_id has an unexpected format")
    msg = ticket.get("merchant_message")
    if not isinstance(msg, str) or not msg.strip():
        problems.append("merchant_message is empty")
    elif len(msg) > MAX_MESSAGE_LEN:
        problems.append(f"merchant_message longer than {MAX_MESSAGE_LEN} characters")
    if ticket.get("channel") not in ("Email", "Chat", "WhatsApp"):
        problems.append("unknown channel")
    mid = ticket.get("merchant_id")
    if mid is not None and not re.fullmatch(r"M\d{4}", str(mid)):
        problems.append("merchant_id has an unexpected format")
    return problems


def parse_amount(raw: str) -> float:
    return float(raw.replace(",", ""))


def extract(message: str) -> dict:
    """Values Python extracts from free text. Used for thresholds, never for identity."""
    payment_ids = PAYMENT_ID_RE.findall(message)
    amounts = [parse_amount(a) for a in AMOUNT_RE.findall(message)]
    days = [int(d) for d in DAYS_RE.findall(message)]
    return {
        "payment_id": payment_ids[0] if payment_ids else None,
        "payment_ids": payment_ids,
        "amount_inr": amounts[0] if amounts else None,
        "amounts_inr": amounts,
        "days_elapsed": days[0] if days else None,
        "error_codes": sorted(set(ERROR_CODE_RE.findall(message))),
        "message_merchant_ids": sorted(set(MERCHANT_ID_RE.findall(message))),
    }


def mask_pii(message: str) -> str:
    """Mask personal data before text leaves the backend. Keeps pay_ IDs and merchant IDs."""
    out = EMAIL_RE.sub("[EMAIL]", message)
    out = CARD_RE.sub(lambda m: "[CARD]" if len(re.sub(r"\D", "", m.group())) >= 13 else m.group(), out)
    out = PHONE_RE.sub("[PHONE]", out)
    out = PAN_RE.sub("[PAN]", out)
    out = ACCOUNT_RE.sub("[ACCOUNT]", out)
    return out


def words(message: str) -> list:
    return re.findall(r"[a-z]+", message.lower())


def hinglish_hits(text: str) -> int:
    return sum(1 for w in words(text) if w in HINGLISH_WORDS)


def detect_language(message: str) -> str:
    return "Hinglish" if hinglish_hits(message) >= 2 else "English"


def detect_tone(message: str) -> str:
    if any(re.search(p, message, re.IGNORECASE) for p in ANGRY_PATTERNS):
        return "angry"
    if any(re.search(p, message, re.IGNORECASE) for p in FRUSTRATED_PATTERNS):
        return "frustrated"
    return "neutral"


def content_word_count(message: str) -> int:
    """Words left after removing IDs, used for the vague-message rule."""
    stripped = MERCHANT_ID_RE.sub(" ", PAYMENT_ID_RE.sub(" ", message))
    stripped = re.sub(r"\b(MID|Merchant ID|Merchant)\b", " ", stripped, flags=re.IGNORECASE)
    return len(re.findall(r"[A-Za-z]+", stripped))


def format_inr(amount) -> str:
    """Indian digit grouping: 250000 -> 2,50,000."""
    n = int(round(float(amount)))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + s
