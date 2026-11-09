# Evidence that source data cannot be modified

## 1. Live Supabase project (ujvptgosuxpvmfptpzzn), privilege check, 09 Nov 2026

Queried with `has_table_privilege` for the pipeline role `ai_processor`:

| Table | SELECT | INSERT | UPDATE | DELETE | TRUNCATE | anon can write | signed-in users can write | guard triggers |
|---|---|---|---|---|---|---|---|---|
| p1_tickets | yes | no | no | no | no | no | no | 3 (block update/delete, block truncate, webhook) |
| p1_merchants | yes | no | no | no | no | no | no | 2 |
| p1_knowledgebase | yes | no | no | no | no | no | no | 2 |
| kb_rule_versions | yes | no | no | no | no | no | no | 1 |
| ai_ticket_analysis | yes | yes | yes | no | no | no | no | 1 (results immutable) |
| ai_jobs | yes | yes | yes | no | no | no | no | 0 |
| ai_triage_reviews | yes | no | no | no | no | no | no | 0 |

`source_integrity()` on the live project: 150 tickets, 60 merchants, 17 KB articles, original rows unchanged.

## 2. Write attempts executed with the real role (local database built from the same migrations)

See `test_results_local.txt`. Every attempt was rejected by PostgreSQL, for example:

```
update p1_tickets ...        -> permission denied for table p1_tickets
delete from p1_knowledgebase -> permission denied for table p1_knowledgebase
insert into p1_merchants ... -> permission denied for table p1_merchants
truncate p1_tickets          -> permission denied for table p1_tickets
alter table p1_tickets add column ai_status text -> must be owner of table p1_tickets
```

Even the table owner is blocked by triggers: `Source table p1_tickets is immutable: UPDATE is not allowed`.
