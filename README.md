# AyNcode

AyNcode is a Flask publishing site for practical writing by Ayotunde Oyeniyi.

## Auto-publish articles from OpenClaw

The repo is set up so an automation runner can generate a real-event article, save it into the repo, commit it, push it to GitHub, and let Vercel redeploy from that push.

Use this command from an OpenClaw cron job:

```bash
cd /home/ayncode/Ay_python_app
bash scripts/openclaw_publish.sh
```

The wrapper will:
- pull the latest `main`
- create/use a local `.venv`
- install/update Python dependencies inside that `.venv`
- generate or update today's article in `content/generated_posts.json`
- commit the content change
- push it to GitHub
- let Vercel redeploy from the push

Recommended environment variables for the OpenClaw cron job:

```bash
AUTO_POST_TOPIC="AI automation for small business"
AUTO_POST_AUDIENCE="founders"
AUTO_POST_ANGLE="Focus on practical tools, risks, and implementation steps."
AUTO_POST_USE_REAL_EVENTS="true"
AUTO_POST_EVENT_QUERY="AI automation business news"
AUTO_POST_EVENT_HOURS="24"
AUTO_POST_USE_GENERATOR_COMMAND="true"
AUTO_POST_GENERATOR_COMMAND=".venv/bin/python scripts/openclaw_codex_article_generator.py"
AUTO_POST_REQUIRE_GENERATOR="true"
AUTO_POST_DYNAMIC_TOPIC="true"
AUTO_POST_MODE="skip"
AUTO_POST_BRANCH="main"
```

`AUTO_POST_MODE=skip` creates one article per topic per day. Use `AUTO_POST_MODE=update` if you want the cron job to replace today's generated article when it runs again.

The publish wrapper sets `AUTO_POST_GENERATOR_COMMAND` to `.venv/bin/python scripts/openclaw_codex_article_generator.py` by default, so Linux cron hosts do not need a bare `python` command. The publisher sends that command JSON on stdin with the topic, audience, angle, 24-hour news events, and writing instructions. The command must print JSON to stdout in this shape:

```json
{
  "title": "Article title",
  "subtitle": "One sentence subtitle.",
  "body": "<p>Article body as clean HTML.</p>"
}
```

For OpenClaw cron, `AUTO_POST_REQUIRE_GENERATOR=true` prevents publishing a fallback template article when Codex fails. `AUTO_POST_DYNAMIC_TOPIC=true` lets the publisher use the top 24-hour headline as the working topic, so daily posts do not keep repeating the same static subject.

Generated posts are stored in `content/generated_posts.json`. On Vercel startup, the app imports any posts from that file that are not already in the database.

## GitHub Actions

The included workflow at `.github/workflows/daily-blog.yml` can also generate, commit, and push daily posts. A push to `main` should trigger Vercel deployment if the Vercel project is connected to this GitHub repo.
