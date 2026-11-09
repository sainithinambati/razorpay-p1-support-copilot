-- Concurrent workers no longer wait on each other when marking a run as running.
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

  -- Non-blocking: concurrent workers never wait on each other for the run row.
  update ai_analysis_runs set status = 'running', started_at = coalesce(started_at, now())
   where run_id in (select r.run_id from ai_analysis_runs r
                     where r.status = 'queued' and (p_run_id is null or r.run_id = p_run_id)
                       and exists (select 1 from ai_jobs where ai_jobs.run_id = r.run_id and ai_jobs.status = 'processing')
                     for update skip locked);
end $$;
