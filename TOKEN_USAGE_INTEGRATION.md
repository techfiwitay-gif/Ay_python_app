# AI usage reporting

AyNcode App Insights accepts daily aggregate AI usage from trusted application backends. It does not accept prompts, responses, API keys, user IDs, or other customer data.

## Configure the website

Create a different random secret for every backend and add one server-side environment variable to the website:

```text
INSIGHTS_INGEST_TOKENS={"6799787039":"getreep-secret-at-least-32-characters","6790227598":"vocalframe-secret-at-least-32-characters"}
```

Known app IDs are `6799787039` for Getreep and `6790227598` for VocalFrame. `openclaw` and `bookafriend` are also available in the dashboard. Never put these ingestion secrets in an iOS, Android, or browser bundle.

## Report daily totals

After an AI provider request completes, the application backend should update its own daily aggregate. It can periodically send the cumulative value to:

```text
POST https://ayncode.com/api/insights/token-usage
Authorization: Bearer <that app's ingestion secret>
Content-Type: application/json
```

```json
{
  "appId": "6799787039",
  "day": "2026-09-23",
  "provider": "openai",
  "model": "gpt-5-mini",
  "inputTokens": 12500,
  "outputTokens": 2300,
  "requestCount": 42,
  "estimatedCostMicros": 18750
}
```

`estimatedCostMicros` is optional and represents millionths of one US dollar. The other totals are required non-negative integers. Reports are keyed by app, UTC day, provider/model, and metric; sending the same key again replaces its totals, so retries are safe.

Provider SDKs name usage fields differently. Normalize the provider response in the backend, then report the aggregate after a successful request. Do not estimate token counts from prompt text in the website.
