-- Database webhook (new ticket -> /api/webhook) and a pg_cron sweeper (every minute -> /api/worker).
-- Secrets are NOT stored here. The owner adds them once in the SQL editor (see docs/SETUP_KEYS.md):
--   select vault.create_secret('<random>', 'copilot_webhook_secret');
--   select vault.create_secret('<random>', 'copilot_worker_secret');
-- Until they exist, both calls are skipped quietly (tickets are still picked up by a manual sweep).

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

create table if not exists public.app_config (
  key   text primary key,
  value text not null
);
alter table public.app_config enable row level security;
revoke all on public.app_config from public, anon, authenticated;
comment on table public.app_config is 'Non-secret settings, for example the public app URL. Secrets live in Supabase Vault.';

create or replace function public.notify_new_ticket() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
declare v_url text; v_secret text;
begin
  if exists (select 1 from ai_submission_scope where ticket_id = new.ticket_id) then
    return new;
  end if;
  select value into v_url from app_config where key = 'app_url';
  select decrypted_secret into v_secret from vault.decrypted_secrets where name = 'copilot_webhook_secret';
  if v_url is null or v_secret is null then
    return new;
  end if;
  perform net.http_post(
    url := rtrim(v_url, '/') || '/api/webhook',
    body := jsonb_build_object('type', 'INSERT', 'table', 'p1_tickets', 'schema', 'public', 'record', to_jsonb(new)),
    headers := jsonb_build_object('Content-Type', 'application/json', 'x-webhook-secret', v_secret),
    timeout_milliseconds := 30000);
  return new;
exception when others then
  -- Never block ticket intake; the sweeper creates the job if the webhook is lost.
  return new;
end $$;

create trigger p1_tickets_webhook after insert on public.p1_tickets
  for each row execute function public.notify_new_ticket();

create or replace function public.run_sweeper() returns void
language plpgsql security definer set search_path = public, extensions as $$
declare v_url text; v_secret text;
begin
  select value into v_url from app_config where key = 'app_url';
  select decrypted_secret into v_secret from vault.decrypted_secrets where name = 'copilot_worker_secret';
  if v_url is null or v_secret is null then
    return;
  end if;
  perform net.http_post(
    url := rtrim(v_url, '/') || '/api/worker',
    body := '{"limit": 5}'::jsonb,
    headers := jsonb_build_object('Content-Type', 'application/json', 'x-worker-secret', v_secret),
    timeout_milliseconds := 60000);
end $$;
revoke all on function public.run_sweeper(), public.notify_new_ticket() from public, anon, authenticated;

select cron.schedule('copilot-sweeper', '* * * * *', 'select public.run_sweeper()');
