# Merchant Support Triage and Resolution Copilot

Razorpay Campus AI Build-a-thon, **Problem 1**. Uses only the synthetic Data Pack.

For each of the 150 tickets the copilot identifies the category, chooses exactly one action
(`AUTO_RESOLVE`, `ASK_FOR_INFO`, `ESCALATE`), selects the escalation team, assigns a risk level and drafts a safe
reply in the merchant's style (English or Hinglish). **Auto-send is disabled:** every reply is a draft for a person.

**Stack:** Supabase (Postgres, Auth, Vault, pg_net, pg_cron) · Python on Vercel (decision engine) · React dashboard on Vercel · Claude API (classification and wording only).

## Who decides what

| Decision | Owner | Why |
|---|---|---|
| Category | Claude (fixed 13-value list, schema-validated), keyword fallback | Language understanding; cannot invent categories |
| Critical-risk signals | Python keywords **or** Claude (union; Claude can add, never remove) | Fail safe |
| Action, team, risk level | Python rules from the KB | Deterministic, testable, reproducible |
| Facts in the reply | Merchant record + KB parameters only | No invented dates, amounts or promises |
| Reply wording | Claude rewrites an approved draft | Tone and language, nothing else |
| Accepting a draft | Python validator | Blocks unsafe drafts; safe fallback used |
| Sending | A human (auto-send off) | Brief: no irreversible action without a human |
| New tickets after the 150 | Support Triage team confirms or corrects | Human-in-the-loop |
| KB changes | Humans, versioned, second-person approval | The AI may only suggest |

## Processing flow

1. **Trigger**: *Run AI Analysis* button (all / remaining / failed, each a new `run_id`) or the Supabase webhook for new tickets.
2. **Durable job**: one row per `(run_id, ticket_id)` in `ai_jobs` (unique). The webhook commits and claims the job before returning HTTP 200.
3. **Atomic claim**: `claim_jobs()` uses `FOR UPDATE SKIP LOCKED` and a lease; max 3 attempts; a pg_cron sweeper picks up anything left behind.
4. **Load** the ticket; the structured `merchant_id` is the authority (IDs in the text are only a consistency check).
5. **Prepare**: validate input, mask PII before it leaves the backend, detect English/Hinglish and tone, extract payment ID, amount, days and error codes with Python.
6. **Classify** (Claude, fixed taxonomy, up to 3 tries, then keyword fallback + human review).
7. **Decide** (Python, in this order): critical-risk gate, identity, mandatory escalations, merchant status and thresholds, missing information, auto-resolve eligibility. Confidence never overrides a rule.
8. **Draft**: build the approved reply from KB statements and merchant facts, then Claude may rephrase it from a fact sheet.
9. **Validate**: every number must already exist in the approved draft or ticket; no promises, no "today", no internal risk reasons, no fault admission, no arguing, no customer blame, no secret-key requests, no other merchant's data, correct language, action-consistent. On failure: draft blocked and stored, safe fallback used, human review required.
10. **Store**: one row per ticket per run in `ai_ticket_analysis` (immutable), logs in `ai_processing_logs`, blocked drafts in `ai_blocked_drafts`, alerts in `ai_alerts`.

## Category taxonomy (13, fixed)

`Settlement`, `Payment Failure`, `Refund`, `Chargeback`, `Fees & Pricing`, `KYC`, `Account Change`, `Account Closure`,
`API & Integration`, `Legal / Regulatory`, `Security`, `Staff Misconduct`, `Unclear`.
When a critical signal is present the critical category wins (Security > Staff Misconduct > Legal / Regulatory) and the
underlying topic is kept in `secondary_topic`.

## Risk scale

| Level | When |
|---|---|
| Critical | Any KB-15 trigger: legal, regulator, media, data security, account takeover, staff bribery/fraud, identity conflict |
| High | Risk-review or chargeback-reserve hold, refund of Rs. 1,00,000 or more, payment success below 85% on a payment ticket, account closure |
| Medium | Other settlement, payment and refund questions, chargebacks, pricing requests, technical faults without a KB fix, missing information, KYC re-verification authenticity |
| Low | Invoices, KYC document list, account detail changes, API errors with a KB fix |

## Escalation teams

| Trigger | Team |
|---|---|
| Legal threat, consumer court/forum, police, RBI/regulator, media, accusing Razorpay of fraud | Legal |
| Data leak, data-security claim, account takeover, identity conflict | Security |
| Staff bribery or commission request | Risk Ops |
| Risk-review or chargeback-reserve hold (KB-03) | Risk Ops |
| Payment ticket with 7-day success below 85% (KB-06) | Payments Ops |
| Refund of Rs. 1,00,000 or more (KB-07) | Refund Ops |
| Pricing, MDR, waiver, fee-difference refund (KB-09) | Account Manager |
| Account closure (KB-17) | Account Manager + Risk Ops |
| Technical fault with no KB fix (rate limits, 5xx, timeouts, blank popup) | Tech Support |

The reply never names Risk Ops; it says "our specialist team".

## KB rule mapping (all 17)

| KB | Rule in `api/_lib/engine.py` | Action |
|---|---|---|
| KB-01 | Settled: share last settlement date and amount from the record, T+2 rule | AUTO_RESOLVE |
| KB-02 | Delayed: share only `expected_settlement_date` | AUTO_RESOLVE |
| KB-03 | Risk review / chargeback reserve: no release, no reason disclosed | ESCALATE Risk Ops |
| KB-04 | KYC pending: Dashboard > Account > KYC, released within 1 business day of verification | AUTO_RESOLVE |
| KB-05 | Bank mismatch: re-add account, penny-drop in 24 hours, no release promise | AUTO_RESOLVE |
| KB-06 | Payment ticket: < 85% escalate; else causes + retry; auto-reversal is not a refund | AUTO_RESOLVE / ESCALATE |
| KB-07 | Refund: no payment ID ask; >= Rs. 1,00,000 escalate, no timeline; else 5-7 days + ARN; never claim individual status | ASK / ESCALATE / AUTO |
| KB-08 | Chargeback: 7 calendar days, approved evidence, cannot block in advance | AUTO_RESOLVE |
| KB-09 | Invoice by the 5th; pricing/waivers to Account Manager | AUTO / ESCALATE |
| KB-10 | Exact document list; re-verification only via Dashboard or @example.com | AUTO_RESOLVE |
| KB-11 | Email/mobile via OTP; legal name or business type needs a fresh application | AUTO_RESOLVE |
| KB-12 | SIGNATURE_MISMATCH on `order_id\|payment_id`; amounts in paise | AUTO_RESOLVE |
| KB-13 | Webhook checklist: public URL, HTTP 200 in 5 s, subscribed event, 24 h retries | AUTO_RESOLVE |
| KB-14 | Authentication failed on live: use live keys; never ask for secrets | AUTO_RESOLVE |
| KB-15 | Critical gate: immediate escalation, safe acknowledgment only | ESCALATE |
| KB-16 | Missing merchant/payment ID asked first; vague messages asked for detail; no unsupported promises; language match | ASK_FOR_INFO |
| KB-17 | Closure: check settlement status, refunds in process, open disputes | ESCALATE AM + Risk Ops |

Thresholds come from the active KB version's `parameters` (baseline kb-v1 = original values), not from code constants.

## Data assumptions

1. `P1_Merchants` is a 30 Sep 2026 snapshot; it is treated as the current status for every ticket (tickets run 01 to 30 Sep). Stored on every run.
2. Every ticket with a structured merchant ID mentions the same ID in its text. Six tickets have no ID at all. There are no conflicts in the data; the conflict check is a safeguard.
3. Refund amounts and payment IDs exist only in the message text, so Python extracts them. `refunds_in_process` is only a count.
4. There are no capture dates, chargeback notice dates or per-refund statuses, so the copilot never computes personalised dates.
5. Risk-review hold duration (KB-03) is not shared with the merchant, because naming the hold type would reveal the internal reason.

## Source data is immutable (enforced by the database)

* Triggers block `UPDATE`, `DELETE` and `TRUNCATE` on all three source tables for **every** role, including the owner, and block `INSERT` into merchants and KB.
* The pipeline role `ai_processor` has `SELECT` only on source tables; it has no service-role key and no admin rights.
* New tickets can only be appended through `submit_ticket()`, which runs as the insert-only `ticket_intake` role.
* `source_integrity()` compares an MD5 fingerprint of the original rows with the copy taken at load time (shown on the dashboard).
* Evidence from the test run is in `docs/test_results_local.txt` (each write attempt and the database's rejection message).

## New tickets: review mode

A new ticket fires the webhook; the same pipeline runs with the **active** KB version and stores the result as a suggestion
(`review_status = pending_triage`). A Support Triage agent confirms it or corrects it with a reason
(`ai_triage_reviews`, the AI row is never overwritten). Critical tickets raise an `ai_alerts` row immediately.

## KB governance

`kb_rule_versions` holds versioned rule sets. Version 1 is a locked copy of the original KB and is always used for
the 150-ticket submission runs. Editors create drafts; a **different** user must approve; approvers activate or roll back.
Safety rules are not parameters, so they cannot be switched off; unknown keys and critical routes pointed elsewhere are rejected.
The AI can only write to `ai_kb_suggestions`.

## Running it

* **Full analysis:** sign in with an operator account > *Run & status* > choose *Run all tickets* > **Run AI Analysis**. Keep the tab open (about 5 to 10 minutes with Claude). If you close it, the sweeper continues at about 5 tickets a minute; *Resume unfinished run* picks it back up.
* **Export:** *150-ticket results* > **Export CSV** or **Export XLSX** (or `GET /api/export?format=xlsx`). Columns: `ticket_id, category, action, escalation_team, risk_level, drafted_reply`.
* **Secrets:** see `docs/SETUP_KEYS.md`.

## Tests

```
pip install -r requirements.txt pytest
python -m pytest tests/test_engine.py          # 72 rule, validation and evaluation tests (no database needed)
# Database tests: build a Postgres from tests/local_supabase_stub.sql + supabase/migrations/*.sql, then
TEST_ADMIN_URL=... DATABASE_URL=<ai_processor url> python -m pytest tests/test_database.py   # 31 tests
```

## Honest limitations

* The evaluation labels were written by the builder from the KB. They are not Razorpay's hidden key, and because the same person wrote the rules, agreement is an upper bound.
* Category accuracy against the hidden key cannot be measured here; nor can we know the key's preferred team names (we map to the brief's vocabulary).
* Hinglish detection is a word list; unusual spellings may be missed.
* The KB has no rule for refunds older than 5-7 business days; such tickets stay `AUTO_RESOLVE` but are flagged for human review.
* Payment-failure spike detection is a simple rule (3 or more merchants, same method, within 2 days). Individual tickets from merchants above 85% are still auto-resolved; the spike view is the signal for Payments Ops.
* External notifications for alerts (email, Slack) are not built; alerts show on the dashboard.
