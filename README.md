# AyNcode

AyNcode is a Flask publishing site for practical writing by Ayotunde Oyeniyi.

## Friday Journal Publisher

The authoritative publisher is the Codex heartbeat automation attached to the
owner's website chat: Friday at 9:15 AM America/New_York. Codex researches,
writes, reviews images and facts, then commits and deploys a complete article.
The computer and Codex app must be running. Do not enable a second publisher.

The previous OpenClaw journal cron is disabled. To remove only that legacy job:

```bash
cd /home/ayncode/Ay_python_app
python3 scripts/configure_journal_cron.py --disable
```

Other cron jobs are preserved; the previous crontab is backed up under
`.git/cron-backups/`. OpenClaw scripts remain as optional diagnostic tools,
not the active scheduled publishing path. Do not reinstall their cron job.

Before publishing, reconcile the latest main branch without discarding local
changes. Use in-memory databases for content checks; do not modify production
analytics or user data. Check for an existing article on today's New York date
to avoid duplicate publication. Keep the four-week archive window and retired
article markers intact.

The reviewed pipeline:
1. Select a credible technology story from the past week (up to two weeks in
   the discovery fallback).
2. Research the exact story in Codex, then fetch readable primary source pages.
   A headline or news-feed snippet is not enough evidence.
3. Write an original, source-linked article explaining the mechanism, limits,
   and a useful decision or worked example.
4. Validate structure, source links, safe HTML and boilerplate rules. Perform
   a separate evidence/usefulness review, allowing at most one revision.
5. Use relevant press artwork with appropriate reuse rights, a licensed image,
   or an original editorial illustration clearly labeled as an illustration.
   Inspect actual pixels for relevance and include attribution. Reject
   misleading namesake, chip, office or data-center substitutions.
6. Save, commit and push only after every check passes. A main push triggers
   the linked Vercel deployment; verify deployment independently.

Research, quality or image failures stop publication. Never substitute filler
or an unrelated visual. Notify the owner with the unresolved problem rather
than publishing a misleading draft. Save at most one reviewed article per run.

### Legacy OpenClaw Diagnostics (Not Scheduled)

The optional wrapper pulls main, locks overlapping runs, loads NVM and installs
dependencies in .venv. It uses an in-memory database. Logs, if manually run,
are in `logs/openclaw_publish.log`.

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
This is an alternative diagnostic runner, not the Codex Friday schedule.
Never commit credentials or .env files.

## Content and Checks

Reviewed articles are saved in `content/generated_posts.json` and imported by
the website. Existing articles retain their stored titles and dates.
Articles are archived after 28 days.

```bash
python -m pytest -q
```
