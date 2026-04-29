# AyNcode

AyNcode is a Flask publishing site for practical writing by Ayotunde Oyeniyi.

## Auto-publish articles from OpenClaw

The repo is set up so an automation runner can generate a real-event article, save it into the repo, commit it, push it to GitHub, and let Vercel redeploy from that push.

Use this command from an OpenClaw cron job after cloning the repo and installing dependencies:

```bash
bash scripts/openclaw_publish.sh
```

The wrapper pulls the latest `main`, generates or updates today's article in `content/generated_posts.json`, commits it, pushes it to GitHub, and lets Vercel redeploy from the push.

Useful environment variables:

```bash
AUTO_POST_TOPIC="AI automation for small business"
AUTO_POST_AUDIENCE="founders"
AUTO_POST_ANGLE="Focus on practical tools, risks, and implementation steps."
AUTO_POST_USE_REAL_EVENTS="true"
AUTO_POST_EVENT_QUERY="AI automation business news"
AUTO_POST_MODE="skip"
```

`AUTO_POST_MODE=skip` creates one article per topic per day. Use `AUTO_POST_MODE=update` if you want the cron job to replace today's generated article when it runs again.

Generated posts are stored in `content/generated_posts.json`. On Vercel startup, the app imports any posts from that file that are not already in the database.

## GitHub Actions

The included workflow at `.github/workflows/daily-blog.yml` can also generate, commit, and push daily posts. A push to `main` should trigger Vercel deployment if the Vercel project is connected to this GitHub repo.
