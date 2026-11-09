-- AI-side storage. Nothing here ever writes to the source tables.

-- ---------------------------------------------------------------------------
-- Human roles (Supabase Auth users)
-- ---------------------------------------------------------------------------
create table public.app_user_roles (
  user_id    uuid not null references auth.users(id) on delete cascade,
  role       text not null check (role in ('operator', 'triage_agent', 'kb_editor', 'kb_approver')),
  granted_at timestamptz not null default now(),
  primary key (user_id, role)
);
alter table public.app_user_roles enable row level security;
revoke all on public.app_user_roles from public, anon, authenticated;
grant select on public.app_user_roles to authenticated;
create policy own_roles on public.app_user_roles for select to authenticated using (user_id = auth.uid());

create or replace function public.has_role(p_role text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from app_user_roles where user_id = auth.uid() and role = p_role);
$$;
create or replace function public.my_roles() returns text[]
language sql stable security definer set search_path = public as $$
  select coalesce(array_agg(role order by role), '{}') from app_user_roles where user_id = auth.uid();
$$;
revoke all on function public.has_role(text), public.my_roles() from public;
grant execute on function public.has_role(text), public.my_roles() to authenticated, ticket_intake;

-- ---------------------------------------------------------------------------
-- KB rule versions (one row = one complete, versioned rule set)
-- ---------------------------------------------------------------------------
create table public.kb_rule_versions (
  version_id         serial primary key,
  rules_version      text not null unique,
  is_baseline        boolean not null default false,
  status             text not null check (status in ('draft', 'approved', 'active', 'retired', 'rejected')),
  articles           jsonb not null,
  parameters         jsonb not null,
  team_mapping       jsonb not null,
  change_reason      text,
  changed_kb_ids     text[] not null default '{}',
  based_on_version   integer references public.kb_rule_versions(version_id),
  created_by         uuid references auth.users(id),
  created_at         timestamptz not null default now(),
  approved_by        uuid references auth.users(id),
  approved_at        timestamptz,
  activated_at       timestamptz
);
create unique index kb_one_active on public.kb_rule_versions ((status)) where status = 'active';

insert into public.kb_rule_versions (rules_version, is_baseline, status, articles, parameters, team_mapping, change_reason, activated_at)
select 'kb-v1', true, 'active',
       jsonb_object_agg(kb_id, jsonb_build_object('topic', topic, 'guidance', guidance)),
       '{
          "settlement_tplus_days": 2,
          "bank_delay_extra_business_days": 2,
          "risk_review_max_business_days": 5,
          "kyc_release_business_days": 1,
          "penny_drop_hours": 24,
          "payment_success_threshold_pct": 85,
          "auto_reversal_business_days": "5-7",
          "refund_credit_business_days": "5-7",
          "arn_available_after_days": 3,
          "high_value_refund_inr": 100000,
          "chargeback_evidence_days": 7,
          "invoice_generation_day": 5,
          "kyc_reverification_months": 24,
          "webhook_timeout_seconds": 5,
          "webhook_retry_hours": 24,
          "confidence_review_threshold": 0.6
        }'::jsonb,
       '{
          "legal": "Legal",
          "security": "Security",
          "identity_conflict": "Security",
          "staff_misconduct": "Risk Ops",
          "settlement_hold": "Risk Ops",
          "payment_degradation": "Payments Ops",
          "high_value_refund": "Refund Ops",
          "pricing": "Account Manager",
          "closure": "Account Manager + Risk Ops",
          "tech_fault": "Tech Support"
        }'::jsonb,
       'Baseline: exact copy of the original P1_KnowledgeBase (17 articles).',
       now()
from public.p1_knowledgebase;

-- Baseline content can never change or be deleted; approved/active content is frozen.
create or replace function public.kb_versions_guard() returns trigger
language plpgsql set search_path = public as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'KB versions are never deleted (version %)', old.rules_version using errcode = '42501';
  end if;
  if old.is_baseline and (new.articles is distinct from old.articles
      or new.parameters is distinct from old.parameters
      or new.team_mapping is distinct from old.team_mapping
      or new.is_baseline is distinct from old.is_baseline
      or new.rules_version is distinct from old.rules_version) then
    raise exception 'The baseline KB version (kb-v1) cannot be edited' using errcode = '42501';
  end if;
  if old.status <> 'draft' and (new.articles is distinct from old.articles
      or new.parameters is distinct from old.parameters
      or new.team_mapping is distinct from old.team_mapping) then
    raise exception 'Only draft KB versions can be edited' using errcode = '42501';
  end if;
  return new;
end $$;
create trigger kb_versions_guard before update or delete on public.kb_rule_versions
  for each row execute function public.kb_versions_guard();

-- Validation of a proposed rule set. Safety floors have no parameter at all, so any
-- unknown key (for example "auto_send_enabled" or "critical_gate_enabled") is rejected.
create or replace function public.kb_validate(p_articles jsonb, p_parameters jsonb, p_team_mapping jsonb)
returns void language plpgsql stable set search_path = public as $$
declare
  base kb_rule_versions;
  k text;
  allowed_teams text[] := array['Legal','Security','Risk Ops','Payments Ops','Refund Ops','Account Manager','Account Manager + Risk Ops','Tech Support'];
  critical_teams text[] := array['Legal','Security','Risk Ops'];
begin
  select * into base from kb_rule_versions where is_baseline;
  if (select array_agg(x order by x) from jsonb_object_keys(p_articles) x)
     is distinct from (select array_agg(x order by x) from jsonb_object_keys(base.articles) x) then
    raise exception 'Articles must be exactly KB-01 to KB-17 (articles cannot be added or removed here)';
  end if;
  for k in select jsonb_object_keys(p_articles) loop
    if coalesce(length(trim(p_articles -> k ->> 'guidance')), 0) < 20 then
      raise exception 'Guidance for % is missing or too short', k;
    end if;
  end loop;
  if (select array_agg(x order by x) from jsonb_object_keys(p_parameters) x)
     is distinct from (select array_agg(x order by x) from jsonb_object_keys(base.parameters) x) then
    raise exception 'Parameters must use exactly the baseline keys. Safety rules (critical gate, no promises, no auto-send, output validation) are not parameters and cannot be changed';
  end if;
  if (p_parameters ->> 'payment_success_threshold_pct')::numeric not between 50 and 100 then
    raise exception 'payment_success_threshold_pct must be between 50 and 100'; end if;
  if (p_parameters ->> 'high_value_refund_inr')::numeric not between 1000 and 10000000 then
    raise exception 'high_value_refund_inr must be between 1000 and 10000000'; end if;
  if (p_parameters ->> 'chargeback_evidence_days')::numeric not between 1 and 60 then
    raise exception 'chargeback_evidence_days must be between 1 and 60'; end if;
  if (p_parameters ->> 'confidence_review_threshold')::numeric not between 0 and 1 then
    raise exception 'confidence_review_threshold must be between 0 and 1'; end if;
  if (select array_agg(x order by x) from jsonb_object_keys(p_team_mapping) x)
     is distinct from (select array_agg(x order by x) from jsonb_object_keys(base.team_mapping) x) then
    raise exception 'team_mapping must use exactly the baseline keys';
  end if;
  for k in select jsonb_object_keys(p_team_mapping) loop
    if not (p_team_mapping ->> k) = any(allowed_teams) then
      raise exception 'Unknown team % for %', p_team_mapping ->> k, k; end if;
  end loop;
  foreach k in array array['legal','security','identity_conflict','staff_misconduct'] loop
    if not (p_team_mapping ->> k) = any(critical_teams) then
      raise exception 'Critical route % must go to Legal, Security or Risk Ops', k; end if;
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Runs, jobs, results
-- ---------------------------------------------------------------------------
create table public.ai_analysis_runs (
  run_id              uuid primary key default gen_random_uuid(),
  run_type            text not null check (run_type in ('batch', 'review')),
  trigger_type        text not null check (trigger_type in ('manual_all', 'manual_remaining', 'manual_failed', 'webhook', 'sweeper')),
  status              text not null default 'queued' check (status in ('queued', 'running', 'completed', 'completed_with_errors')),
  rules_version       text not null,
  kb_version_id       integer not null references public.kb_rule_versions(version_id),
  model_name          text,
  snapshot_assumption text not null default 'P1_Merchants is a 30 Sep 2026 snapshot and is treated as the current system status for every ticket.',
  total_jobs          integer not null default 0,
  created_by          uuid references auth.users(id),
  created_at          timestamptz not null default now(),
  started_at          timestamptz,
  completed_at        timestamptz
);

create table public.ai_jobs (
  job_id       bigserial primary key,
  run_id       uuid not null references public.ai_analysis_runs(run_id),
  ticket_id    text not null references public.p1_tickets(ticket_id),
  status       text not null default 'pending' check (status in ('pending', 'processing', 'done', 'failed')),
  attempts     integer not null default 0,
  max_attempts integer not null default 3 check (max_attempts between 1 and 3),
  lease_until  timestamptz,
  last_error   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (run_id, ticket_id)
);
create index ai_jobs_claim_idx on public.ai_jobs (status, lease_until);

create table public.ai_ticket_analysis (
  analysis_id               uuid primary key default gen_random_uuid(),
  run_id                    uuid not null references public.ai_analysis_runs(run_id),
  job_id                    bigint references public.ai_jobs(job_id),
  ticket_id                 text not null references public.p1_tickets(ticket_id),
  merchant_id               text,
  -- mandatory output
  category                  text not null,
  action                    text not null check (action in ('AUTO_RESOLVE', 'ASK_FOR_INFO', 'ESCALATE')),
  escalation_team           text,
  risk_level                text not null check (risk_level in ('Critical', 'High', 'Medium', 'Low')),
  drafted_reply             text not null,
  -- evidence
  secondary_topic           text,
  risk_signals              text[] not null default '{}',
  language_style            text check (language_style in ('English', 'Hinglish')),
  tone                      text check (tone in ('neutral', 'frustrated', 'angry')),
  confidence                numeric,
  classifier_source         text check (classifier_source in ('claude', 'keyword_fallback')),
  payment_id                text,
  refund_amount_inr         numeric,
  relevant_kb_ids           text[] not null default '{}',
  merchant_facts_used       jsonb not null default '{}',
  decision_trace            jsonb not null default '[]',
  escalation_team_primary   text,
  escalation_team_secondary text,
  validation_status         text not null check (validation_status in ('passed', 'failed_fallback', 'processing_failed')),
  human_review_required     boolean not null default false,
  failure_reason            text,
  model_name                text,
  rules_version             text not null,
  auto_send_enabled         boolean not null default false check (auto_send_enabled = false),
  review_status             text not null default 'not_required'
                            check (review_status in ('not_required', 'pending_triage', 'routed')),
  processed_at              timestamptz not null default now(),
  unique (run_id, ticket_id),
  check (action <> 'ESCALATE' or escalation_team is not null),
  check (action = 'ESCALATE' or escalation_team is null),
  check (length(trim(drafted_reply)) > 0),
  check (length(trim(category)) > 0)
);

-- The AI's suggestion is immutable once written: corrections live in ai_triage_reviews.
create or replace function public.analysis_guard() returns trigger
language plpgsql set search_path = public as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'Analysis history is never deleted' using errcode = '42501';
  end if;
  if (new.category, new.action, new.escalation_team, new.risk_level, new.drafted_reply, new.run_id, new.ticket_id)
     is distinct from (old.category, old.action, old.escalation_team, old.risk_level, old.drafted_reply, old.run_id, old.ticket_id) then
    raise exception 'Stored AI results are immutable; record corrections in ai_triage_reviews' using errcode = '42501';
  end if;
  return new;
end $$;
create trigger analysis_guard before update or delete on public.ai_ticket_analysis
  for each row execute function public.analysis_guard();

create table public.ai_processing_logs (
  log_id     bigserial primary key,
  run_id     uuid references public.ai_analysis_runs(run_id),
  job_id     bigint references public.ai_jobs(job_id),
  ticket_id  text,
  stage      text not null,
  level      text not null default 'info' check (level in ('info', 'warning', 'error')),
  message    text not null,
  details    jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index ai_logs_run_idx on public.ai_processing_logs (run_id, created_at);

create table public.ai_blocked_drafts (
  blocked_id       bigserial primary key,
  analysis_id      uuid references public.ai_ticket_analysis(analysis_id),
  run_id           uuid references public.ai_analysis_runs(run_id),
  ticket_id        text not null,
  failed_draft     text not null,
  unsafe_statement text not null,
  reason           text not null,
  violated_kb_id   text,
  fallback_action  text not null,
  required_team    text,
  created_at       timestamptz not null default now()
);

create table public.ai_triage_reviews (
  review_id             bigserial primary key,
  analysis_id           uuid not null references public.ai_ticket_analysis(analysis_id),
  reviewer              uuid not null references auth.users(id),
  decision              text not null check (decision in ('confirmed', 'corrected')),
  corrections           jsonb not null default '{}',
  final_category        text not null,
  final_action          text not null,
  final_escalation_team text,
  final_risk_level      text not null,
  final_reply           text not null,
  reason                text,
  created_at            timestamptz not null default now(),
  check (decision = 'confirmed' or length(trim(coalesce(reason, ''))) > 0)
);

create table public.ai_alerts (
  alert_id        bigserial primary key,
  analysis_id     uuid references public.ai_ticket_analysis(analysis_id),
  run_id          uuid references public.ai_analysis_runs(run_id),
  ticket_id       text not null,
  team            text not null,
  risk_signals    text[] not null default '{}',
  message         text not null,
  raised_at       timestamptz not null default now(),
  acknowledged_by uuid references auth.users(id),
  acknowledged_at timestamptz
);

create table public.ai_kb_suggestions (
  suggestion_id bigserial primary key,
  run_id        uuid references public.ai_analysis_runs(run_id),
  ticket_id     text,
  kb_id         text,
  topic         text not null,
  suggestion    text not null,
  status        text not null default 'open' check (status in ('open', 'accepted', 'dismissed')),
  created_at    timestamptz not null default now()
);

create table public.ai_eval_labels (
  ticket_id           text primary key references public.p1_tickets(ticket_id),
  case_type           text not null,
  expected_category   text not null,
  expected_action     text not null check (expected_action in ('AUTO_RESOLVE', 'ASK_FOR_INFO', 'ESCALATE')),
  expected_team       text,
  expected_risk_level text not null,
  expected_kb_ids     text[] not null default '{}',
  notes               text,
  labeled_by          text not null default 'Builder (manual label from KB rules); not Razorpay''s hidden key'
);

-- ---------------------------------------------------------------------------
-- Privileges and RLS
-- ---------------------------------------------------------------------------
revoke all on public.kb_rule_versions, public.ai_analysis_runs, public.ai_jobs, public.ai_ticket_analysis,
              public.ai_processing_logs, public.ai_blocked_drafts, public.ai_triage_reviews, public.ai_alerts,
              public.ai_kb_suggestions, public.ai_eval_labels
  from public, anon, authenticated;

-- Public dashboard: read-only.
grant select on public.kb_rule_versions, public.ai_analysis_runs, public.ai_jobs, public.ai_ticket_analysis,
                public.ai_processing_logs, public.ai_triage_reviews, public.ai_alerts,
                public.ai_kb_suggestions, public.ai_eval_labels
  to anon, authenticated;
-- Blocked drafts: anon sees the summary columns only; the full failed draft is for signed-in users.
grant select (blocked_id, analysis_id, run_id, ticket_id, unsafe_statement, reason, violated_kb_id,
              fallback_action, required_team, created_at) on public.ai_blocked_drafts to anon;
grant select on public.ai_blocked_drafts to authenticated;

-- AI processing role: read sources + rules, write AI tables, never delete, never touch KB versions or triage.
grant select on public.kb_rule_versions, public.ai_eval_labels to ai_processor;
grant select, insert, update on public.ai_analysis_runs, public.ai_jobs, public.ai_ticket_analysis to ai_processor;
grant select, insert on public.ai_processing_logs, public.ai_blocked_drafts, public.ai_alerts, public.ai_kb_suggestions to ai_processor;
grant usage, select on sequence public.ai_jobs_job_id_seq, public.ai_processing_logs_log_id_seq,
                     public.ai_blocked_drafts_blocked_id_seq, public.ai_alerts_alert_id_seq,
                     public.ai_kb_suggestions_suggestion_id_seq to ai_processor;

alter table public.kb_rule_versions enable row level security;
alter table public.ai_analysis_runs enable row level security;
alter table public.ai_jobs enable row level security;
alter table public.ai_ticket_analysis enable row level security;
alter table public.ai_processing_logs enable row level security;
alter table public.ai_blocked_drafts enable row level security;
alter table public.ai_triage_reviews enable row level security;
alter table public.ai_alerts enable row level security;
alter table public.ai_kb_suggestions enable row level security;
alter table public.ai_eval_labels enable row level security;

create policy read_all on public.kb_rule_versions for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_analysis_runs for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_jobs for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_ticket_analysis for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_processing_logs for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_blocked_drafts for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_triage_reviews for select to anon, authenticated using (true);
create policy read_all on public.ai_alerts for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_kb_suggestions for select to anon, authenticated, ai_processor using (true);
create policy read_all on public.ai_eval_labels for select to anon, authenticated, ai_processor using (true);

create policy proc_insert on public.ai_analysis_runs for insert to ai_processor with check (true);
create policy proc_update on public.ai_analysis_runs for update to ai_processor using (true) with check (true);
create policy proc_insert on public.ai_jobs for insert to ai_processor with check (true);
create policy proc_update on public.ai_jobs for update to ai_processor using (true) with check (true);
create policy proc_insert on public.ai_ticket_analysis for insert to ai_processor with check (true);
create policy proc_update on public.ai_ticket_analysis for update to ai_processor using (true) with check (true);
create policy proc_insert on public.ai_processing_logs for insert to ai_processor with check (true);
create policy proc_insert on public.ai_blocked_drafts for insert to ai_processor with check (true);
create policy proc_insert on public.ai_alerts for insert to ai_processor with check (true);
create policy proc_insert on public.ai_kb_suggestions for insert to ai_processor with check (true);
