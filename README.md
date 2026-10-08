# AyNcode

AyNcode is a Flask publishing site for practical writing by Ayotunde Oyeniyi.

## Friday Journal Publisher

The authoritative publisher runs on the owner's WSL host using OpenClaw's
existing authenticated model. No separate OpenAI API key is required for this
path. Keep the host running and OpenClaw authenticated.

Install or repair only the journal crontab entry, Friday at 9:15 AM
America/New_York, including daylight-saving changes:

```bash
cd /home/ayncode/Ay_python_app
python3 scripts/configure_journal_cron.py --install
bash scripts/openclaw_publish.sh --dry-run --preview-path /tmp/ayncode-preview.json
# The live scheduled command:
bash scripts/openclaw_publish.sh
```

Other cron jobs are preserved. The previous crontab is backed up under
`.git/cron-backups/`. Do not enable a second GitHub scheduled publisher.

The wrapper pulls main with --ff-only, locks overlapping runs, loads the current
NVM Node version, and installs Python dependencies in .venv. It uses an
in-memory database, not the website database or its local schema migrations.

The reviewed pipeline:
1. Select a credible technology story from the past week (up to two weeks in
   the discovery fallback).
2. Ask OpenClaw to research the exact story, then fetch readable source pages.
   A headline or news-feed snippet is not enough evidence.
3. Write an original, source-linked article explaining the mechanism, limits,
   and a useful decision or worked example.
4. Validate structure, source links, safe HTML and boilerplate rules. Perform
   a separate evidence/usefulness review, allowing at most one revision.
5. Prefer the exact publisher page's artwork, then a licensed Commons image.
   Inspect actual pixels for relevance and include attribution. Reject
   misleading namesake, chip, office or data-center substitutions.
6. Save, commit and push only after every check passes. A main push triggers
   the linked Vercel deployment; verify deployment independently.

Research, authentication, model, quality or image failures stop publication
with a nonzero exit code. No template or generic-image fallback is permitted
in the scheduled flow. Logs: `logs/openclaw_publish.log`. Model judgments can
still be wrong, so these checks improve reliability rather than guarantee it.

`AUTO_POST_MODE=skip` prevents another article on the same date. Dry runs never
save repo content, commit or push. Review their JSON preview locally.
`AUTO_POST_MODE=update` is an explicit override; article identities still depend
on generated titles.

Settings: `AUTO_POST_EVENT_HOURS=168`, `AUTO_POST_FALLBACK_EVENT_HOURS=336`,
`AUTO_POST_REQUIRE_GENERATOR=true`, `AUTO_POST_EDITORIAL_BACKEND=openclaw`.
Leave `OPENCLAW_ARTICLE_MODEL` unset to use OpenClaw's configured model, or supply
a valid configured provider/model. Each call gets a separate session.

## Optional GitHub Manual Runner

`.github/workflows/daily-blog.yml` is manual-only, dry-run by default. It uses
the direct OpenAI Responses API backend and requires the GitHub repository
secret `OPENAI_API_KEY`. `AUTO_POST_OPENAI_MODEL` defaults to `gpt-6-luna`.
This is an alternative diagnostic runner, not the OpenClaw Friday schedule.
Never commit credentials or .env files.

## Content and Checks

Reviewed articles are saved in `content/generated_posts.json` and imported by
the website. Existing articles retain their stored titles and dates.
Articles are archived after 28 days.

```bash
python -m pytest -q
```
