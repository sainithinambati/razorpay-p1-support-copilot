-- Original Razorpay Problem 1 source tables.
-- Column names and values mirror the Data Pack exactly (dates kept as the original text).
-- These tables are read-only for every routine role (see 0003).

create table public.p1_tickets (
  ticket_id        text primary key,
  created_at       text not null,
  channel          text not null,
  merchant_id      text,
  merchant_message text not null
);

create table public.p1_merchants (
  merchant_id                 text primary key,
  business_name               text not null,
  segment                     text,
  industry                    text,
  city                        text,
  last_settlement_date        text,
  last_settlement_amount_inr  numeric,
  settlement_status           text,
  hold_reason                 text,
  hold_since                  text,
  expected_settlement_date    text,
  payment_success_rate_7d_pct numeric,
  open_chargebacks            integer,
  refunds_in_process          integer,
  kyc_status                  text,
  gst_invoice_available       text
);

create table public.p1_knowledgebase (
  kb_id    text primary key,
  topic    text not null,
  guidance text not null
);

comment on table public.p1_tickets is 'Original P1_Tickets (immutable source). New tickets may only be appended by the ticket_intake role.';
comment on table public.p1_merchants is 'Original P1_Merchants (immutable source, snapshot 30 Sep 2026).';
comment on table public.p1_knowledgebase is 'Original P1_KnowledgeBase (immutable source). Edits go to kb_rule_versions.';
