# Setting up the secrets (do this yourself, never paste them into chat or commit them)

Everything that is safe to be public (Supabase URL, anon key, app URL) is already configured.
You add four secrets. Total time: about 10 minutes.

| # | Secret | Where it goes |
|---|---|---|
| 1 | `ai_processor` database password, inside `DATABASE_URL` | Supabase SQL editor (password) + Vercel env var |
| 2 | `ANTHROPIC_API_KEY` | Vercel env var |
| 3 | `WORKER_SECRET` | Vercel env var + Supabase Vault |
| 4 | `WEBHOOK_SECRET` | Vercel env var + Supabase Vault |

In Vercel, add every variable under **Project > Settings > Environment Variables**, tick **Production** and **Preview**,
and mark it **Sensitive**. Never give these a `VITE_` prefix (that would put them in the browser).

## 1. Database credential for the AI pipeline (`DATABASE_URL`)

The pipeline does not use the admin `postgres` user or the service-role key. It uses a restricted role,
`ai_processor`, that can read the source tables and write only the AI tables.

1. Make a strong password (for example from a password manager, letters and digits only, 32 characters).
2. Supabase Dashboard > **SQL Editor** > New query, run:
   ```sql
   alter role ai_processor with login password 'PASTE-YOUR-PASSWORD-HERE';
   ```
3. Supabase Dashboard > **Connect** (top bar) > **Transaction pooler**. Copy the URI. It looks like
   `postgresql://postgres.ujvptgosuxpvmfptpzzn:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres`
4. Change the user from `postgres.ujvptgosuxpvmfptpzzn` to `ai_processor.ujvptgosuxpvmfptpzzn` and put your password in:
   `postgresql://ai_processor.ujvptgosuxpvmfptpzzn:YOUR-PASSWORD@aws-0-ap-south-1.pooler.supabase.com:6543/postgres`
5. Vercel: add `DATABASE_URL` with that value.

## 2. Claude API key (`ANTHROPIC_API_KEY`)

1. console.anthropic.com > **API Keys** > Create key.
2. Vercel: add `ANTHROPIC_API_KEY`.
3. Recommended: console.anthropic.com > **Limits**, set a monthly spend limit (for example USD 10).
   A full 150-ticket run makes about 300 short calls.
4. Optional: `CLAUDE_MODEL` (default `claude-sonnet-5-5`).

Without this key the system still works: it uses the keyword classifier and the approved reply templates.

## 3 and 4. Worker and webhook secrets

These let Supabase call your Vercel API and prove it is really Supabase.

1. Make two different random values (password manager, 40+ letters and digits each).
2. Vercel: add `WORKER_SECRET` = first value, `WEBHOOK_SECRET` = second value.
3. Supabase SQL Editor, run (with your values):
   ```sql
   select vault.create_secret('FIRST-VALUE',  'copilot_worker_secret');
   select vault.create_secret('SECOND-VALUE', 'copilot_webhook_secret');
   ```
   To change one later: `select vault.update_secret((select id from vault.secrets where name = 'copilot_worker_secret'), 'NEW-VALUE');`

## 5. Redeploy and check

1. Vercel > **Deployments** > latest > **Redeploy** (env vars only apply to new deployments).
2. Open `https://<your-app>/api/health`. You should see `database.connected: true`, `role: ai_processor`,
   `claude_configured: true`, both secrets `true`, and `auto_send_enabled: false`. It never shows the values.

## 6. User accounts and roles

1. On the dashboard, click **Sign in > Create account** twice, with two different emails
   (KB changes need a second person to approve). Confirm the emails if Supabase asks.
2. Tell Claude the two emails, or run this yourself in the SQL editor:
   ```sql
   insert into app_user_roles (user_id, role)
   select id, r from auth.users, unnest(array['operator','triage_agent','kb_editor']) r where email = 'first@example.com';
   insert into app_user_roles (user_id, role)
   select id, r from auth.users, unnest(array['kb_approver','triage_agent']) r where email = 'second@example.com';
   ```

## If a secret leaks

Rotate it immediately: new Anthropic key in the console; `alter role ai_processor password '...'` for the database;
`vault.update_secret` plus the Vercel variable for the two shared secrets. Then redeploy.
Because the repository is public, run `git log -p | grep -i -E "sk-ant|password|secret"` before every push.
