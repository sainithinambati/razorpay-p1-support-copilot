-- Fixes found by the local integration tests.
-- 1. kb_create_draft: appending to text[] needs array_append.
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
  if p_parameters is distinct from base.parameters then v_changed := array_append(v_changed, 'PARAMETERS'); end if;
  if p_team_mapping is distinct from base.team_mapping then v_changed := array_append(v_changed, 'ROUTING'); end if;
  if cardinality(v_changed) = 0 then raise exception 'No changes compared with the base version'; end if;

  insert into kb_rule_versions (rules_version, status, articles, parameters, team_mapping, change_reason,
                                changed_kb_ids, based_on_version, created_by)
  values ('kb-v' || ((select max(version_id) from kb_rule_versions) + 1), 'draft', p_articles, p_parameters,
          p_team_mapping, trim(p_reason), v_changed, p_based_on, auth.uid())
  returning version_id into v_id;
  return v_id;
end $$;

-- 2. The processing role may READ triage decisions (needed by v_triage_queue); it still cannot write them.
grant select on public.ai_triage_reviews to ai_processor;
alter policy read_all on public.ai_triage_reviews to anon, authenticated, ai_processor;
