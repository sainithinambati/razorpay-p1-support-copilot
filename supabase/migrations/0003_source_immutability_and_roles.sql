-- Immutability of source data, enforced by the database (not by app code).

-- 1. Baseline fingerprint of the original data, taken right after seeding.
create table public.ai_source_baseline (
  table_name  text primary key,
  row_count   integer not null,
  content_md5 text not null,
  captured_at timestamptz not null default now()
);

insert into public.ai_source_baseline (table_name, row_count, content_md5)
select 'p1_tickets', count(*), md5(string_agg(t::text, '|' order by ticket_id)) from public.p1_tickets t
union all
select 'p1_merchants', count(*), md5(string_agg(m::text, '|' order by merchant_id)) from public.p1_merchants m
union all
select 'p1_knowledgebase', count(*), md5(string_agg(k::text, '|' order by kb_id)) from public.p1_knowledgebase k;

-- The 150 original tickets define the submission scope.
create table public.ai_submission_scope (
  ticket_id text primary key references public.p1_tickets(ticket_id)
);
insert into public.ai_submission_scope select ticket_id from public.p1_tickets;

-- 2. Triggers: no UPDATE / DELETE / TRUNCATE on any source table, for any role.
--    No INSERT into merchants or KB. Tickets accept INSERT only (new tickets).
create or replace function public.block_source_write() returns trigger
language plpgsql set search_path = public as $$
begin
  raise exception 'Source table % is immutable: % is not allowed', tg_table_name, tg_op
    using errcode = '42501';
end $$;

create trigger p1_tickets_no_update before update or delete on public.p1_tickets
  for each row execute function public.block_source_write();
create trigger p1_tickets_no_truncate before truncate on public.p1_tickets
  for each statement execute function public.block_source_write();

create trigger p1_merchants_no_write before insert or update or delete on public.p1_merchants
  for each row execute function public.block_source_write();
create trigger p1_merchants_no_truncate before truncate on public.p1_merchants
  for each statement execute function public.block_source_write();

create trigger p1_kb_no_write before insert or update or delete on public.p1_knowledgebase
  for each row execute function public.block_source_write();
create trigger p1_kb_no_truncate before truncate on public.p1_knowledgebase
  for each statement execute function public.block_source_write();

-- 3. Roles.
--    ai_processor: used by the Python pipeline. Login password is set by the owner in the
--                  Supabase SQL editor (never committed). Read-only on source tables.
--    ticket_intake: owns the submit_ticket() function; INSERT-only on p1_tickets.
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'ai_processor') then
    create role ai_processor nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'ticket_intake') then
    create role ticket_intake nologin noinherit;
  end if;
end $$;

grant usage on schema public to ai_processor, ticket_intake;

-- 4. Privileges on source tables: strip Supabase defaults, then grant SELECT only.
revoke all on public.p1_tickets, public.p1_merchants, public.p1_knowledgebase,
              public.ai_source_baseline, public.ai_submission_scope
  from public, anon, authenticated;

grant select on public.p1_tickets, public.p1_merchants, public.p1_knowledgebase,
                public.ai_source_baseline, public.ai_submission_scope
  to anon, authenticated, ai_processor;

grant select, insert on public.p1_tickets to ticket_intake;

-- 5. Row Level Security on source tables (read for everyone, no write policies at all).
alter table public.p1_tickets enable row level security;
alter table public.p1_merchants enable row level security;
alter table public.p1_knowledgebase enable row level security;
alter table public.ai_source_baseline enable row level security;
alter table public.ai_submission_scope enable row level security;

create policy read_tickets on public.p1_tickets for select to anon, authenticated, ai_processor, ticket_intake using (true);
create policy intake_insert_tickets on public.p1_tickets for insert to ticket_intake with check (true);
create policy read_merchants on public.p1_merchants for select to anon, authenticated, ai_processor using (true);
create policy read_kb on public.p1_knowledgebase for select to anon, authenticated, ai_processor using (true);
create policy read_baseline on public.ai_source_baseline for select to anon, authenticated, ai_processor using (true);
create policy read_scope on public.ai_submission_scope for select to anon, authenticated, ai_processor using (true);

-- 6. Integrity check: compare current source data against the baseline fingerprint.
create or replace function public.source_integrity()
returns table (table_name text, baseline_rows int, current_rows int,
               original_rows_unchanged boolean, appended_rows int, checked_at timestamptz)
language sql stable security definer set search_path = public as $$
  with cur as (
    select 'p1_tickets'::text tn,
           (select count(*) from p1_tickets)::int cnt,
           (select md5(string_agg(t::text, '|' order by t.ticket_id)) from p1_tickets t
              where t.ticket_id in (select ticket_id from ai_submission_scope)) h
    union all
    select 'p1_merchants', (select count(*) from p1_merchants)::int,
           (select md5(string_agg(m::text, '|' order by merchant_id)) from p1_merchants m)
    union all
    select 'p1_knowledgebase', (select count(*) from p1_knowledgebase)::int,
           (select md5(string_agg(k::text, '|' order by kb_id)) from p1_knowledgebase k)
  )
  select b.table_name, b.row_count, c.cnt, (c.h = b.content_md5), c.cnt - b.row_count, now()
  from ai_source_baseline b join cur c on c.tn = b.table_name
  order by b.table_name;
$$;
revoke all on function public.source_integrity() from public;
grant execute on function public.source_integrity() to anon, authenticated, ai_processor;
