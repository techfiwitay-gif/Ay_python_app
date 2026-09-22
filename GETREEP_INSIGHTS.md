# App Insights — AyNcode admin

The dashboard is served at /admin/getreep. Sign in through the existing /admin
page and choose **App Insights**. HTML, JSON, imports, and Apple sync all
require the existing administrator email/role and active session. POST requests
also require the session CSRF token. Responses are private/no-store/noindex.
The React bundle contains no credentials or customer data.

## Server configuration (never put these in frontend variables)

- INSIGHTS_DATABASE_URL: Neon PostgreSQL pooled connection, provisioned by the
  Vercel integration with the INSIGHTS prefix (Production only). A separate
  SQLAlchemy bind creates insight_metrics, app_insight_metrics, and insight_state here. The existing
  DATABASE_URL, website users, posts, and admin login storage stay unchanged.
  Without this setting, analytics uses a temporary in-memory development store;
  production counting remains disabled. No historical data is migrated implicitly.
  Connection and statement timeouts are bounded. Analytics database failures
  return a generic reporting error and do not prevent website startup or login.
- INSIGHTS_TRACKING_ENABLED=1: enable aggregate public page-view and app-specific
  App Store redirect counts. Disabled automatically on Vercel temporary SQLite.
- ASC_ISSUER_ID, ASC_KEY_ID, ASC_PRIVATE_KEY: dedicated App Store Connect team API
  key for reporting. The private key accepts PEM with actual or escaped newlines.
  Initial ongoing analytics-report creation needs Admin permission; report
  retrieval supports a suitable reporting role. App discovery lists all accessible
  apps; each app needs an ongoing report request activated by an Admin once.
  Production uses the dedicated Sales and Reports key, not the setup Admin key.
- GETREEP_SUPABASE_URL, GETREEP_SUPABASE_SERVICE_ROLE_KEY: Getreep's server-only
  Supabase connection. This is a privileged key: restrict deployment access.
  Code performs reads only, selecting entitlement status and profile names.
  A dedicated least-privilege reporting proxy is preferable if available.

Missing connections are explicitly unavailable, not fake zero totals.
Do not paste keys into chat, commit them, or upload them as CSV files.

## Reporting behavior and limits

- Website counts start only after enabling tracking; no historical backfill.
  They are server-observed page views, not sessions/unique visitors. Cached
  responses may not reach the counter. Known bots, admin views, HEAD, prefetch,
  DNT, and Global Privacy Control are excluded. No IP, cookie, user agent,
  visitor ID, URL query, or arbitrary path is stored. App Store redirects are
  fixed and cannot be used as an open redirect. Public company, product,
  support, journal archive/article, and OpenClaw pages are counted. Missing
  pages and administrative routes are not counted.
- The Application selector offers All apps and individual apps. Sync refreshes
  the authenticated Apple app catalog. All apps syncs each app sequentially in
  separate requests; counts, processing versions, and sync status stay isolated.
  Legacy app metrics remain attributed to Getreep. General website views appear
  only in All apps; /getreep and /vocalframe views and redirects belong to their app.
- Apple sync is on demand, not a scheduled job. Each app request ingests at most one
  complete instance for each of the two standard reports. All segments must
  succeed before committing. Existing Admin-requested one-time snapshots can
  supply historical instances when the ongoing feed has none. More clicks can
  backfill available instances. Sync status distinguishes reports that Apple
  has not generated from daily instances already processed.
  Reports can take 24–48 hours to first appear; counts lag and are privacy-limited.
  New processing batches replace older daily totals; they are never added twice.
  App Store search includes Search Ads and does not prove exact brand ranking.
- The API/download loop is bounded. Oversized reports or unsupported download
  hosts fail without modifying existing reports. Larger volumes need a background
  worker. Live Apple report fetching must be acceptance-tested with the real key.
- CSV imports use the normalized template, not raw Apple exports. Supported
  metrics: first_downloads, redownloads, impressions, page_views,
  website_page_views, download_clicks. Dates use YYYY-MM-DD; counts are integers.
  Choose an individual app before importing. Reimporting replaces matching manual
  totals for that app only. Live data supersedes manual totals for that app.
- Subscriber integration currently supports Getreep only; other apps explicitly
  show that their subscriber backend is not connected. Apple reports do not supply
  customer identities. Subscribers are linked Supabase accounts, not unique paying people. Production,
  sandbox, and unknown environments remain separate. Guests/deleted accounts can
  be missing; RevenueCat aliases can duplicate purchases. Expiry indicates access,
  not renewal/trial/cancellation status. No emails, trips, documents, or mailbox
  data are loaded. An optional profile-name read failure leaves valid entitlement
  rows visible without names. The list fails explicitly above 10,000 accounts.

## Build and verify

Run npm ci and npm run build in insights-ui. Commit the resulting static files.
Run .venv/Scripts/python.exe -m pytest -q from the website checkout.
The production Flask deployment serves the built bundle; no Node server needed.
Keep API keys out of preview deployments unless explicitly scoped for testing.
Check the live login boundary and backend connection status before calling
reporting operational. No Getreep iOS build or TestFlight upload is involved.
