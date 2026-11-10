-- Public demo: anyone can start a batch run without signing in.
-- Two guards protect the prepaid Claude credit (about USD 1.80 per full run):
--   1. Only one batch run at a time (no new run while jobs are pending or processing).
--   2. At most N runs started without signing in per 24 hours (app_config key 'public_runs_per_day', default 4).
-- The worker only processes jobs already queued by this function, so an anonymous caller
-- can never cause more Claude calls than start_run allowed.

insert into public.app_config (key, value) values ('public_runs_per_day', '4')
on conflict (key) do nothing;

create or replace function public.start_run(p_mode text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_run uuid; v_kb kb_rule_versions; n int; v_cap int; v_today int;
begin
  if p_mode not in ('all', 'remaining', 'failed') then
    raise exception 'Mode must be all, remaining or failed';
  end if;

  -- Serialise run creation so two clicks cannot both pass the guards.
  perform pg_advisory_xact_lock(hashtext('start_run'));

  if exists (select 1 from ai_jobs j join ai_analysis_runs r on r.run_id = j.run_id
              where r.run_type = 'batch' and j.status in ('pending', 'processing')
                and j.updated_at > now() - interval '15 minutes') then
    raise exception 'A run is already in progress. Wait for it to finish, or choose Resume unfinished run.';
  end if;

  select coalesce(nullif(value, '')::int, 4) into v_cap from app_config where key = 'public_runs_per_day';
  v_cap := coalesce(v_cap, 4);
  select count(*) into v_today from ai_analysis_runs
   where run_type = 'batch' and created_by is null and created_at > now() - interval '24 hours';
  if v_today >= v_cap then
    raise exception 'The daily limit of % runs has been reached. The latest results are still available.', v_cap;
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

grant execute on function public.start_run(text) to anon, authenticated;
