-- Functions (RPCs) and views. Every human action checks the caller's role inside the database.

-- ---------------------------------------------------------------------------
-- Job queue (used by the ai_processor role)
-- ---------------------------------------------------------------------------
create or replace function public.claim_jobs(p_run_id uuid, p_limit int default 3, p_lease_seconds int default 150)
returns setof public.ai_jobs
language plpgsql set search_path = public as $$
begin
  -- Jobs whose lease expired after their last allowed attempt are marked failed (no unlimited retries).
  update ai_jobs set status = 'failed', updated_at = now(),
         last_error = coalesce(last_error, 'Lease expired after final attempt')
   where status = 'processing' and lease_until < now() and attempts >= max_attempts
     and (p_run_id is null or run_id = p_run_id);

  return query
  with c as (
    select job_id from ai_jobs
     where (p_run_id is null or run_id = p_run_id)
       and (status = 'pending'
            or (status = 'processing' and lease_until < now() and attempts < max_attempts))
     order by job_id
     for update skip locked
     limit greatest(1, least(p_limit, 10))
  )
  update ai_jobs j
     set status = 'processing', attempts = j.attempts + 1,
         lease_until = now() + make_interval(secs => p_lease_seconds), updated_at = now()
    from c where j.job_id = c.job_id
  returning j.*;

  update ai_analysis_runs set status = 'running', started_at = coalesce(started_at, now())
   where status = 'queued' and (p_run_id is null or run_id = p_run_id)
     and exists (select 1 from ai_jobs where ai_jobs.run_id = ai_analysis_runs.run_id and ai_jobs.status = 'processing');
end $$;

create or replace function public.finish_run_if_done(p_run_id uuid) returns text
language plpgsql set search_path = public as $$
declare v_status text;
begin
  if exists (select 1 from ai_jobs where run_id = p_run_id and status in ('pending', 'processing')) then
    return 'running';
  end if;
  v_status := case when exists (select 1 from ai_jobs where run_id = p_run_id and status = 'failed')
                   then 'completed_with_errors' else 'completed' end;
  update ai_analysis_runs set status = v_status, completed_at = coalesce(completed_at, now())
   where run_id = p_run_id and status <> v_status;
  return v_status;
end $$;

-- Webhook path: durable review job for a new ticket, created and claimed atomically.
-- Duplicate deliveries for the same ticket return the existing job instead of a second one.
create or replace function public.create_review_job(p_ticket_id text, p_trigger text default 'webhook')
returns table (job_id bigint, run_id uuid, attempts int, status text, newly_claimed boolean)
language plpgsql set search_path = public as $$
declare
  v_job ai_jobs;
  v_run uuid;
  v_kb kb_rule_versions;
begin
  perform pg_advisory_xact_lock(hashtext('review:' || p_ticket_id));
  if not exists (select 1 from p1_tickets where ticket_id = p_ticket_id) then
    raise exception 'Unknown ticket %', p_ticket_id;
  end if;
  if exists (select 1 from ai_submission_scope where ticket_id = p_ticket_id) then
    raise exception 'Ticket % is one of the 150 batch tickets; use a batch run', p_ticket_id;
  end if;

  select j.* into v_job from ai_jobs j join ai_analysis_runs r on r.run_id = j.run_id
   where j.ticket_id = p_ticket_id and r.run_type = 'review'
   order by j.job_id desc limit 1 for update of j;

  if found then
    if v_job.status = 'pending'
       or (v_job.status = 'processing' and v_job.lease_until < now() and v_job.attempts < v_job.max_attempts) then
      update ai_jobs set status = 'processing', attempts = ai_jobs.attempts + 1,
             lease_until = now() + interval '150 seconds', updated_at = now()
       where ai_jobs.job_id = v_job.job_id returning * into v_job;
      return query select v_job.job_id, v_job.run_id, v_job.attempts, v_job.status, true;
    else
      return query select v_job.job_id, v_job.run_id, v_job.attempts, v_job.status, false;
    end if;
    return;
  end if;

  select * into v_kb from kb_rule_versions where kb_rule_versions.status = 'active';
  insert into ai_analysis_runs (run_type, trigger_type, status, rules_version, kb_version_id, total_jobs, started_at)
  values ('review', p_trigger, 'running', v_kb.rules_version, v_kb.version_id, 1, now())
  returning ai_analysis_runs.run_id into v_run;

  insert into ai_jobs (run_id, ticket_id, status, attempts, lease_until)
  values (v_run, p_ticket_id, 'processing', 1, now() + interval '150 seconds')
  returning * into v_job;

  return query select v_job.job_id, v_job.run_id, v_job.attempts, v_job.status, true;
end $$;

-- Sweeper safety net: any appended ticket that never got a job (for example a lost webhook) gets one.
create or replace function public.sweep_unqueued_tickets() returns int
language plpgsql set search_path = public as $$
declare t record; n int := 0; v_kb kb_rule_versions; v_run uuid;
begin
  select * into v_kb from kb_rule_versions where status = 'active';
  for t in
    select p.ticket_id from p1_tickets p
     where not exists (select 1 from ai_submission_scope s where s.ticket_id = p.ticket_id)
       and not exists (select 1 from ai_jobs j where j.ticket_id = p.ticket_id)
  loop
    perform pg_advisory_xact_lock(hashtext('review:' || t.ticket_id));
    insert into ai_analysis_runs (run_type, trigger_type, rules_version, kb_version_id, total_jobs)
    values ('review', 'sweeper', v_kb.rules_version, v_kb.version_id, 1) returning run_id into v_run;
    insert into ai_jobs (run_id, ticket_id) values (v_run, t.ticket_id);
    n := n + 1;
  end loop;
  return n;
end $$;

revoke all on function public.claim_jobs(uuid, int, int), public.finish_run_if_done(uuid),
                       public.create_review_job(text, text), public.sweep_unqueued_tickets() from public;
grant execute on function public.claim_jobs(uuid, int, int), public.finish_run_if_done(uuid),
                          public.create_review_job(text, text), public.sweep_unqueued_tickets() to ai_processor;

-- ---------------------------------------------------------------------------
-- Run AI Analysis button (operator role). Batch runs are pinned to the baseline KB (kb-v1).
-- ---------------------------------------------------------------------------
create or replace function public.start_run(p_mode text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_run uuid; v_kb kb_rule_versions; n int;
begin
  if not has_role('operator') then
    raise exception 'Only users with the operator role can start a run' using errcode = '42501';
  end if;
  if p_mode not in ('all', 'remaining', 'failed') then
    raise exception 'Mode must be all, remaining or failed';
  end if;
  select * into v_kb from kb_rule_versions where is_baseline;

  insert into ai_analysis_runs (run_type, trigger_type, rules_version, kb_version_id, created_by)
  values ('batch', 'manual_' || p_mode, v_kb.rules_version, v_kb.version_id, auth.uid())
  returning run_id into v_run;

  insert into ai_jobs (run_id, ticket_id)
  select v_run, s.ticket_id from ai_submission_scope s
   where p_mode = 'all'
      or (p_mode = 'remaining'
          and not exists (select 1 from ai_ticket_analysis a join ai_analysis_runs r on r.run_id = a.run_id
                           where a.ticket_id = s.ticket_id and r.run_type = 'batch'
                             and a.validation_status <> 'processing_failed')
          and not exists (select 1 from ai_jobs j join ai_analysis_runs r on r.run_id = j.run_id
                           where j.ticket_id = s.ticket_id and r.run_type = 'batch' and j.run_id <> v_run
                             and j.status in ('pending', 'processing')))
      or (p_mode = 'failed'
          and not exists (select 1 from ai_ticket_analysis a join ai_analysis_runs r on r.run_id = a.run_id
                           where a.ticket_id = s.ticket_id and r.run_type = 'batch'
                             and a.validation_status <> 'processing_failed')
          and (exists (select 1 from ai_ticket_analysis a join ai_analysis_runs r on r.run_id = a.run_id
                        where a.ticket_id = s.ticket_id and r.run_type = 'batch' and a.validation_status = 'processing_failed')
               or exists (select 1 from ai_jobs j join ai_analysis_runs r on r.run_id = j.run_id
                           where j.ticket_id = s.ticket_id and r.run_type = 'batch' and j.status = 'failed')))
   order by s.ticket_id;
  get diagnostics n = row_count;

  if n = 0 then
    raise exception 'Nothing to process for mode %', p_mode;
  end if;
  update ai_analysis_runs set total_jobs = n where run_id = v_run;
  return v_run;
end $$;

-- ---------------------------------------------------------------------------
-- New ticket intake (runs with ticket_intake privileges: INSERT-only on p1_tickets)
-- ---------------------------------------------------------------------------
create or replace function public.submit_ticket(p_merchant_id text, p_channel text, p_message text)
returns text language plpgsql security definer set search_path = public as $$
declare v_id text;
begin
  if not (has_role('operator') or has_role('triage_agent')) then
    raise exception 'Only operators or triage agents can submit test tickets' using errcode = '42501';
  end if;
  if p_channel not in ('Email', 'Chat', 'WhatsApp') then raise exception 'Channel must be Email, Chat or WhatsApp'; end if;
  if length(trim(coalesce(p_message, ''))) = 0 or length(p_message) > 2000 then
    raise exception 'Message must be 1 to 2000 characters'; end if;
  perform pg_advisory_xact_lock(hashtext('ticket_id_seq'));
  select 'T-' || lpad((max(substr(ticket_id, 3)::int) + 1)::text, 4, '0') into v_id from p1_tickets;
  insert into p1_tickets (ticket_id, created_at, channel, merchant_id, merchant_message)
  values (v_id, to_char(now() at time zone 'Asia/Kolkata', 'DD Mon YYYY HH24:MI'), p_channel,
          nullif(trim(p_merchant_id), ''), trim(p_message));
  return v_id;
end $$;
grant ticket_intake to postgres;
-- Ownership needs CREATE on the schema at the moment of ALTER OWNER only; it is revoked straight after.
grant create on schema public to ticket_intake;
alter function public.submit_ticket(text, text, text) owner to ticket_intake;
revoke create on schema public from ticket_intake;

-- ---------------------------------------------------------------------------
-- Support Triage decisions and alerts
-- ---------------------------------------------------------------------------
create or replace function public.triage_decide(p_analysis_id uuid, p_decision text, p_corrections jsonb, p_reason text)
returns bigint language plpgsql security definer set search_path = public as $$
declare
  a ai_ticket_analysis; v_review bigint;
  f_cat text; f_act text; f_team text; f_risk text; f_reply text;
  cats text[] := array['Settlement','Payment Failure','Refund','Chargeback','Fees & Pricing','KYC','Account Change',
                       'Account Closure','API & Integration','Legal / Regulatory','Security','Staff Misconduct','Unclear'];
  teams text[] := array['Legal','Security','Risk Ops','Payments Ops','Refund Ops','Account Manager','Account Manager + Risk Ops','Tech Support'];
  k text;
begin
  if not has_role('triage_agent') then
    raise exception 'Only Support Triage agents can review tickets' using errcode = '42501';
  end if;
  select * into a from ai_ticket_analysis where analysis_id = p_analysis_id for update;
  if not found then raise exception 'Unknown analysis'; end if;
  if a.review_status <> 'pending_triage' then raise exception 'This ticket is not waiting for triage'; end if;
  if p_decision not in ('confirmed', 'corrected') then raise exception 'Decision must be confirmed or corrected'; end if;
  p_corrections := coalesce(p_corrections, '{}'::jsonb);
  if p_decision = 'confirmed' and p_corrections <> '{}'::jsonb then
    raise exception 'A confirmation cannot include corrections'; end if;
  if p_decision = 'corrected' then
    if p_corrections = '{}'::jsonb then raise exception 'A correction must change at least one field'; end if;
    if length(trim(coalesce(p_reason, ''))) = 0 then raise exception 'A reason is required for corrections'; end if;
  end if;
  for k in select jsonb_object_keys(p_corrections) loop
    if k not in ('category', 'action', 'escalation_team', 'risk_level', 'drafted_reply') then
      raise exception 'Field % cannot be corrected', k; end if;
  end loop;

  f_cat   := coalesce(p_corrections ->> 'category', a.category);
  f_act   := coalesce(p_corrections ->> 'action', a.action);
  f_team  := case when p_corrections ? 'escalation_team' then nullif(p_corrections ->> 'escalation_team', '') else a.escalation_team end;
  f_risk  := coalesce(p_corrections ->> 'risk_level', a.risk_level);
  f_reply := coalesce(p_corrections ->> 'drafted_reply', a.drafted_reply);

  if not f_cat = any(cats) then raise exception 'Unknown category %', f_cat; end if;
  if f_act not in ('AUTO_RESOLVE', 'ASK_FOR_INFO', 'ESCALATE') then raise exception 'Unknown action %', f_act; end if;
  if f_risk not in ('Critical', 'High', 'Medium', 'Low') then raise exception 'Unknown risk level %', f_risk; end if;
  if f_act = 'ESCALATE' and (f_team is null or not f_team = any(teams)) then
    raise exception 'An escalation needs a valid team'; end if;
  if f_act <> 'ESCALATE' then f_team := null; end if;
  if a.risk_level = 'Critical' and f_act = 'AUTO_RESOLVE' then
    raise exception 'Critical tickets cannot be auto-resolved (KB-15)'; end if;
  if length(trim(f_reply)) = 0 then raise exception 'Reply cannot be empty'; end if;

  insert into ai_triage_reviews (analysis_id, reviewer, decision, corrections, final_category, final_action,
                                 final_escalation_team, final_risk_level, final_reply, reason)
  values (a.analysis_id, auth.uid(), p_decision, p_corrections, f_cat, f_act, f_team, f_risk, f_reply, nullif(trim(p_reason), ''))
  returning review_id into v_review;

  update ai_ticket_analysis set review_status = 'routed' where analysis_id = a.analysis_id;
  return v_review;
end $$;

create or replace function public.ack_alert(p_alert_id bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not has_role('triage_agent') then
    raise exception 'Only Support Triage agents can acknowledge alerts' using errcode = '42501';
  end if;
  update ai_alerts set acknowledged_by = auth.uid(), acknowledged_at = now()
   where alert_id = p_alert_id and acknowledged_at is null;
end $$;

-- ---------------------------------------------------------------------------
-- KB governance (humans only; the AI role has no execute rights here)
-- ---------------------------------------------------------------------------
create or replace function public.kb_create_draft(p_based_on int, p_articles jsonb, p_parameters jsonb,
                                                  p_team_mapping jsonb, p_reason text)
returns int language plpgsql security definer set search_path = public as $$
declare base kb_rule_versions; v_id int; v_changed text[];
begin
  if not has_role('kb_editor') then
    raise exception 'Only KB editors can create drafts' using errcode = '42501';
  end if;
  if length(trim(coalesce(p_reason, ''))) < 5 then raise exception 'A change reason is required'; end if;
  select * into base from kb_rule_versions where version_id = p_based_on;
  if not found then raise exception 'Unknown base version'; end if;
  perform kb_validate(p_articles, p_parameters, p_team_mapping);

  select coalesce(array_agg(k order by k), '{}') into v_changed
    from jsonb_object_keys(p_articles) k
   where p_articles -> k is distinct from base.articles -> k;
  if p_parameters is distinct from base.parameters then v_changed := v_changed || 'PARAMETERS'; end if;
  if p_team_mapping is distinct from base.team_mapping then v_changed := v_changed || 'ROUTING'; end if;
  if cardinality(v_changed) = 0 then raise exception 'No changes compared with the base version'; end if;

  insert into kb_rule_versions (rules_version, status, articles, parameters, team_mapping, change_reason,
                                changed_kb_ids, based_on_version, created_by)
  values ('kb-v' || ((select max(version_id) from kb_rule_versions) + 1), 'draft', p_articles, p_parameters,
          p_team_mapping, trim(p_reason), v_changed, p_based_on, auth.uid())
  returning version_id into v_id;
  return v_id;
end $$;

create or replace function public.kb_approve(p_version_id int) returns void
language plpgsql security definer set search_path = public as $$
declare v kb_rule_versions;
begin
  if not has_role('kb_approver') then
    raise exception 'Only KB approvers can approve' using errcode = '42501';
  end if;
  select * into v from kb_rule_versions where version_id = p_version_id for update;
  if not found or v.status <> 'draft' then raise exception 'Only drafts can be approved'; end if;
  if v.created_by = auth.uid() then
    raise exception 'You cannot approve your own KB edit; a second person must approve it' using errcode = '42501';
  end if;
  perform kb_validate(v.articles, v.parameters, v.team_mapping);
  update kb_rule_versions set status = 'approved', approved_by = auth.uid(), approved_at = now()
   where version_id = p_version_id;
end $$;

create or replace function public.kb_reject(p_version_id int) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not has_role('kb_approver') then
    raise exception 'Only KB approvers can reject' using errcode = '42501';
  end if;
  update kb_rule_versions set status = 'rejected' where version_id = p_version_id and status = 'draft';
end $$;

create or replace function public.kb_activate(p_version_id int) returns void
language plpgsql security definer set search_path = public as $$
declare v kb_rule_versions;
begin
  if not has_role('kb_approver') then
    raise exception 'Only KB approvers can activate a version' using errcode = '42501';
  end if;
  select * into v from kb_rule_versions where version_id = p_version_id for update;
  if not found or v.status not in ('approved', 'retired') then
    raise exception 'Only approved (or previously active) versions can be activated'; end if;
  update kb_rule_versions set status = 'retired' where status = 'active';
  update kb_rule_versions set status = 'active', activated_at = now() where version_id = p_version_id;
end $$;

revoke all on function public.start_run(text), public.submit_ticket(text, text, text),
                       public.triage_decide(uuid, text, jsonb, text), public.ack_alert(bigint),
                       public.kb_create_draft(int, jsonb, jsonb, jsonb, text), public.kb_approve(int),
                       public.kb_reject(int), public.kb_activate(int), public.kb_validate(jsonb, jsonb, jsonb)
  from public, anon;
grant execute on function public.start_run(text), public.submit_ticket(text, text, text),
                          public.triage_decide(uuid, text, jsonb, text), public.ack_alert(bigint),
                          public.kb_create_draft(int, jsonb, jsonb, jsonb, text), public.kb_approve(int),
                          public.kb_reject(int), public.kb_activate(int)
  to authenticated;
grant execute on function public.kb_validate(jsonb, jsonb, jsonb) to authenticated;

-- ---------------------------------------------------------------------------
-- Views (security_invoker: the caller's own grants and RLS apply)
-- ---------------------------------------------------------------------------
create view public.v_current_results with (security_invoker = true) as
select distinct on (a.ticket_id)
       a.*, t.merchant_message, t.channel, t.created_at as ticket_created_at, r.trigger_type
  from public.ai_ticket_analysis a
  join public.ai_analysis_runs r on r.run_id = a.run_id and r.run_type = 'batch'
  join public.ai_submission_scope s on s.ticket_id = a.ticket_id
  join public.p1_tickets t on t.ticket_id = a.ticket_id
 order by a.ticket_id, (a.validation_status = 'processing_failed'), a.processed_at desc;

create view public.v_submission_table with (security_invoker = true) as
select ticket_id, category, action, coalesce(escalation_team, '') as escalation_team, risk_level, drafted_reply
  from public.v_current_results order by ticket_id;

create view public.v_run_progress with (security_invoker = true) as
select r.run_id, r.run_type, r.trigger_type, r.status, r.rules_version, r.model_name, r.total_jobs,
       r.created_at, r.started_at, r.completed_at,
       count(j.*) filter (where j.status = 'pending')    as pending,
       count(j.*) filter (where j.status = 'processing') as processing,
       count(j.*) filter (where j.status = 'done')       as done,
       count(j.*) filter (where j.status = 'failed')     as failed
  from public.ai_analysis_runs r left join public.ai_jobs j on j.run_id = r.run_id
 group by r.run_id;

create view public.v_triage_queue with (security_invoker = true) as
select distinct on (a.ticket_id)
       a.*, t.merchant_message, t.channel, t.created_at as ticket_created_at,
       tr.decision as triage_decision, tr.final_category, tr.final_action, tr.final_escalation_team,
       tr.final_risk_level, tr.final_reply, tr.reason as triage_reason, tr.created_at as triaged_at,
       case a.risk_level when 'Critical' then 1 when 'High' then 2 when 'Medium' then 3 else 4 end as risk_rank
  from public.ai_ticket_analysis a
  join public.ai_analysis_runs r on r.run_id = a.run_id and r.run_type = 'review'
  join public.p1_tickets t on t.ticket_id = a.ticket_id
  left join lateral (select * from public.ai_triage_reviews x where x.analysis_id = a.analysis_id
                      order by x.created_at desc limit 1) tr on true
 order by a.ticket_id, a.processed_at desc;

create view public.v_eval_results with (security_invoker = true) as
select l.ticket_id, l.case_type, c.merchant_message,
       l.expected_category, c.category as actual_category, (l.expected_category = c.category) as category_match,
       l.expected_action, c.action as actual_action, (l.expected_action = c.action) as action_match,
       l.expected_team, c.escalation_team as actual_team,
       (coalesce(l.expected_team, '') = coalesce(c.escalation_team, '')) as team_match,
       l.expected_risk_level, c.risk_level as actual_risk_level, (l.expected_risk_level = c.risk_level) as risk_match,
       l.expected_kb_ids, c.relevant_kb_ids as actual_kb_ids,
       c.validation_status, c.failure_reason, c.drafted_reply, l.notes,
       (l.expected_action = 'ESCALATE' and l.expected_risk_level = 'Critical' and c.action = 'AUTO_RESOLVE') as critical_auto_resolved
  from public.ai_eval_labels l left join public.v_current_results c on c.ticket_id = l.ticket_id
 order by l.ticket_id;

create view public.v_payment_failure_spikes with (security_invoker = true) as
with pf as (
  select c.ticket_id, c.merchant_id,
         to_date(substr(c.ticket_created_at, 1, 11), 'DD Mon YYYY') as d,
         case when c.merchant_message ~* 'upi' then 'UPI' when c.merchant_message ~* 'card' then 'Card' else 'Unspecified' end as method
    from public.v_current_results c
   where c.category = 'Payment Failure' and c.merchant_id is not null
)
select days.day, pf.method, count(distinct pf.merchant_id) as merchants,
       array_agg(distinct pf.ticket_id) as tickets
  from (select distinct d as day from pf) days
  join pf on pf.d between days.day - 1 and days.day
 group by days.day, pf.method
having count(distinct pf.merchant_id) >= 3
 order by days.day;

grant select on public.v_current_results, public.v_submission_table, public.v_run_progress,
                public.v_triage_queue, public.v_eval_results, public.v_payment_failure_spikes
  to anon, authenticated, ai_processor;
