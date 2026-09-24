import csv
import io
from datetime import date
import pytest
from test_app import app_module, client, create_user, create_post, login
from insights import (parse_import, parse_token_usage, subscriber_records,
                      supabase_key_role, SubscriberSourceError,
                      estimated_ai_cost_microusd)
from apple_reports import aggregate_reports, allowed_segment_url, AppleReportError, APP_ID, sync_reports


def authenticate(app_module, client):
    with app_module.app.app_context():
        create_user(app_module, role="admin")
    login(client)


def test_private_routes_require_admin(app_module, client):
    response = client.get("/admin/getreep")
    assert response.status_code == 302 and "/admin?" in response.location
    for path, method in [("dashboard", "get"), ("import", "post"), ("sync/apple", "post")]:
        response = getattr(client, method)("/admin/getreep/api/" + path)
        assert response.status_code == 401
        assert "no-store" in response.headers["Cache-Control"]
    assert client.get("/admin/getreep/metrics-template.csv").status_code == 302
    authenticate(app_module, client)
    with app_module.app.app_context():
        user = app_module.Users.query.filter_by(email="admin@example.com").one()
        user.role = "user"
        app_module.db.session.commit()
    # Existing user loader revokes the session when the admin role is removed.
    assert client.get("/admin/getreep").status_code == 302
    assert client.get("/admin/getreep/api/dashboard").status_code == 401


def test_admin_page_and_empty_data(app_module, client, monkeypatch):
    monkeypatch.delenv("GETREEP_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    authenticate(app_module, client)
    assert b"App Insights" in client.get("/admin").data
    page = client.get("/admin/getreep")
    assert page.status_code == 200 and b'csrf-token' in page.data
    assert "no-store" in page.headers["Cache-Control"]
    body = client.get("/admin/getreep/api/dashboard").json
    assert body["metrics"] == [] and body["subscribers"] == []
    assert body["connections"]["subscribers"]["configured"] is False


def test_import_atomic_and_idempotent(app_module, client):
    authenticate(app_module, client)
    day = date.today().isoformat()
    csv = f"day,metric,source,value\n{day},first_downloads,Search,3\n"
    for _ in range(2):
        assert client.post("/admin/getreep/api/import", data=csv).json["rows"] == 1
    records = client.get("/admin/getreep/api/dashboard").json["metrics"]
    assert len(records) == 1 and records[0]["value"] == 3
    assert client.post("/admin/getreep/api/import", data=csv + "bad,row\n").status_code == 400
    assert client.get("/admin/getreep/api/dashboard").json["metrics"] == records


def test_token_usage_requires_per_app_server_secret(app_module, client, monkeypatch):
    token = "a" * 32
    monkeypatch.setenv("INSIGHTS_INGEST_TOKENS", '{"6799787039":"' + token + '"}')
    payload = {"appId": "6799787039", "day": date.today().isoformat(), "provider": "openai",
               "model": "gpt-5-mini", "inputTokens": 120, "outputTokens": 30,
               "requestCount": 2, "estimatedCostMicros": 75}
    assert client.post("/api/insights/token-usage", json=payload).status_code == 401
    assert client.post("/api/insights/token-usage", json=payload,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.post("/api/insights/token-usage", json=payload,
                           headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert response.json == {"saved": True, "appId": "6799787039", "day": payload["day"],
                             "source": "openai / gpt-5-mini"}


def test_dashboard_estimates_missing_luna_cost(app_module, client, monkeypatch):
    token = "a" * 32
    monkeypatch.setenv("INSIGHTS_INGEST_TOKENS", '{"6799787039":"' + token + '"}')
    payload = {"appId": "6799787039", "day": date.today().isoformat(), "provider": "openai",
               "model": "gpt-6-luna", "inputTokens": 31643, "outputTokens": 787,
               "requestCount": 2}
    assert client.post("/api/insights/token-usage", json=payload,
                       headers={"Authorization": "Bearer " + token}).status_code == 200
    authenticate(app_module, client)
    metrics = client.get("/admin/getreep/api/dashboard").json["metrics"]
    costs = [row for row in metrics if row["metric"] == "ai_cost_microusd"]
    assert costs == [{"appId": "6799787039", "day": payload["day"],
                      "metric": "ai_cost_microusd", "source": "openai / gpt-6-luna",
                      "value": 3558}]


def test_published_luna_costs_are_calculated_in_micro_usd():
    assert estimated_ai_cost_microusd("openai / gpt-6-luna", 31643, 787) == 3558
    assert estimated_ai_cost_microusd("openai / gpt-5.6-luna", 1000000, 1000000) == 1400000
    assert estimated_ai_cost_microusd("other / gpt-6-luna", 100, 100) is None


def test_token_usage_is_idempotent_and_separated_by_app(app_module, client, monkeypatch):
    tokens = {"6799787039": "g" * 32, "6790227598": "v" * 32}
    monkeypatch.setenv("INSIGHTS_INGEST_TOKENS", __import__("json").dumps(tokens))
    day = date.today().isoformat()
    for app_id, input_tokens in (("6799787039", 100), ("6790227598", 300)):
        payload = {"appId": app_id, "day": day, "provider": "openai", "model": "gpt-5-mini",
                   "inputTokens": input_tokens, "outputTokens": 20, "requestCount": 2,
                   "estimatedCostMicros": 50}
        headers = {"Authorization": "Bearer " + tokens[app_id]}
        assert client.post("/api/insights/token-usage", json=payload, headers=headers).status_code == 200
        payload["inputTokens"] += 1
        assert client.post("/api/insights/token-usage", json=payload, headers=headers).status_code == 200
        payload["inputTokens"] -= 50
        assert client.post("/api/insights/token-usage", json=payload, headers=headers).status_code == 200
    authenticate(app_module, client)
    body = client.get("/admin/getreep/api/dashboard").json
    rows = [row for row in body["metrics"] if row["metric"] == "ai_input_tokens"]
    assert {(row["appId"], row["value"]) for row in rows} == {
        ("6799787039", 101), ("6790227598", 301)}
    assert body["connections"]["aiUsage"]["configured"] is True
    assert body["connections"]["aiUsage"]["lastSync"] is not None


@pytest.mark.parametrize("change", [
    {"prompt": "do not store me"}, {"inputTokens": -1}, {"requestCount": True},
    {"day": "2099-01-01"}, {"provider": "bad\nprovider"},
])
def test_token_usage_rejects_content_and_invalid_totals(change):
    payload = {"appId": "6799787039", "day": date.today().isoformat(), "provider": "openai",
               "model": "gpt-5-mini", "inputTokens": 10, "outputTokens": 5, "requestCount": 1}
    payload.update(change)
    with pytest.raises(ValueError):
        parse_token_usage(payload)


def test_import_and_sync_need_csrf(app_module, client):
    authenticate(app_module, client)
    app_module.app.config["WTF_CSRF_ENABLED"] = True
    for path in ("import", "sync/apple"):
        assert client.post("/admin/getreep/api/" + path).status_code == 400


def test_import_accepts_page_csrf_token(app_module, client):
    from bs4 import BeautifulSoup
    authenticate(app_module, client)
    app_module.app.config["WTF_CSRF_ENABLED"] = True
    page = BeautifulSoup(client.get("/admin/getreep").data, "html.parser")
    token = page.select_one('meta[name="csrf-token"]')["content"]
    payload = f"day,metric,source,value\n{date.today().isoformat()},first_downloads,Search,3\n"
    response = client.post("/admin/getreep/api/import", data=payload,
                           headers={"X-CSRFToken": token, "Origin": "http://localhost"})
    assert response.status_code == 200
    assert client.post("/admin/getreep/api/import", data=payload,
                       headers={"X-CSRFToken": token, "Origin": "https://untrusted.example"}).status_code == 403


def test_reporting_tables_are_isolated_and_persistent(app_module, client):
    from sqlalchemy import inspect
    authenticate(app_module, client)
    day = date.today().isoformat()
    assert client.post("/admin/getreep/api/import", data=f"day,metric,source,value\n{day},first_downloads,Search,7\n").status_code == 200
    with app_module.app.app_context():
        primary = set(inspect(app_module.db.engine).get_table_names())
        reporting = set(inspect(app_module.db.engines["insights"]).get_table_names())
        assert "users" in primary and "insight_metrics" not in primary
        assert reporting == {"insight_metrics", "insight_state", "app_insight_metrics"}
        app_module.db.session.remove()
        app_module.db.engines["insights"].dispose()
    assert client.get("/admin/getreep/api/dashboard").json["metrics"][0]["value"] == 7


def test_reporting_outage_does_not_break_public_pages(app_module, client, monkeypatch):
    monkeypatch.setenv("INSIGHTS_TRACKING_ENABLED", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    with app_module.app.app_context():
        app_module.db.drop_all(bind_key="insights")
    assert client.get("/getreep", headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
    assert client.get("/go/getreep", headers={"User-Agent": "Mozilla/5.0"}).status_code == 302
    authenticate(app_module, client)
    assert client.get("/admin").status_code == 200
    response = client.get("/admin/getreep/api/dashboard")
    assert response.status_code == 503
    assert "temporarily unavailable" in response.json["error"]
    assert "no-store" in response.headers["Cache-Control"]


def test_production_refuses_temporary_reporting(app_module, client, monkeypatch):
    authenticate(app_module, client)
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("INSIGHTS_DATABASE_URL", raising=False)
    assert client.get("/admin/getreep/api/dashboard").status_code == 503
    assert client.post("/admin/getreep/api/import", data="unused").status_code == 503


def test_reporting_timeout_is_transaction_local(app_module):
    from unittest.mock import Mock
    connection = Mock()
    app_module.set_reporting_statement_timeout(connection)
    connection.exec_driver_sql.assert_called_once_with("SET LOCAL statement_timeout = '5s'")


@pytest.mark.parametrize("line", [
    "2099-01-01,first_downloads,Search,3",
    "2026-02-30,first_downloads,Search,3",
    "2026-01-01,first_downloads,Search,-1",
    "2026-01-01,unsupported,Search,3",
    "2026-01-01,first_downloads,,3",
])
def test_bad_imports(line):
    with pytest.raises(ValueError):
        parse_import("day,metric,source,value\n" + line)


def test_counter_and_redirect(app_module, client, monkeypatch):
    monkeypatch.setenv("INSIGHTS_TRACKING_ENABLED", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    headers = {"User-Agent": "Mozilla/5.0 Mobile Safari"}
    client.get("/getreep", headers=headers)
    client.get("/getreep", headers=headers)
    client.get("/getreep", headers={**headers, "DNT": "1"})
    client.get("/getreep", headers={**headers, "Sec-GPC": "1"})
    client.get("/getreep", headers={"User-Agent": "Googlebot"})
    client.get("/getreep", headers={**headers, "Purpose": "prefetch"})
    client.head("/getreep", headers=headers)
    response = client.get("/go/getreep?next=https://attacker.example", headers=headers)
    assert response.location == app_module.GETREEP_APP_STORE_URL
    authenticate(app_module, client)
    rows = client.get("/admin/getreep/api/dashboard").json["metrics"]
    assert sum(r["value"] for r in rows if r["metric"] == "website_page_views") == 2
    assert sum(r["value"] for r in rows if r["metric"] == "download_clicks") == 1


def test_counter_includes_journal_and_other_public_pages(app_module, client, monkeypatch):
    monkeypatch.setenv("INSIGHTS_TRACKING_ENABLED", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    with app_module.app.app_context():
        author = create_user(app_module, email="writer@example.com")
        post_id = create_post(app_module, author).id
    headers = {"User-Agent": "Mozilla/5.0"}
    for path in ("/archive", f"/post/{post_id}?campaign=test", "/open-claw", "/contact"):
        assert client.get(path, headers=headers).status_code == 200
    assert client.get("/post/999999", headers=headers).status_code == 404
    authenticate(app_module, client)
    rows = client.get("/admin/getreep/api/dashboard").json["metrics"]
    sources = {r["source"] for r in rows if r["metric"] == "website_page_views"}
    assert sources == {"/archive", f"/post/{post_id}", "/openclaw", "/contact"}


def test_subscriber_source_reports_safe_http_reason(monkeypatch):
    import requests
    monkeypatch.setenv("GETREEP_SUPABASE_URL", "https://ccitgqgjaktzpydqjulm.supabase.co")
    monkeypatch.setenv("GETREEP_SUPABASE_SERVICE_ROLE_KEY", "private-test-key")
    class Denied:
        status_code = 401
        def raise_for_status(self):
            raise requests.HTTPError("private-test-key", response=self)
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Denied())
    with pytest.raises(SubscriberSourceError) as failure:
        subscriber_records()
    assert (failure.value.stage, failure.value.reason) == ("subscription_entitlements", "http-401")
    assert "private-test-key" not in str(failure.value)


def test_missing_optional_profile_does_not_hide_subscriber(monkeypatch):
    import requests
    monkeypatch.setenv("GETREEP_SUPABASE_URL", "https://ccitgqgjaktzpydqjulm.supabase.co")
    monkeypatch.setenv("GETREEP_SUPABASE_SERVICE_ROLE_KEY", "private-test-key")
    class Response:
        def __init__(self, path):
            self.path = path
        def raise_for_status(self):
            if self.path.endswith("/profiles"):
                raise requests.HTTPError("hidden", response=self)
        @property
        def status_code(self):
            return 404
        def json(self):
            return [{"user_id": "11111111-1111-1111-1111-111111111111", "product_id": "plus",
                     "environment": "PRODUCTION", "expires_at": "2099-01-01T00:00:00Z",
                     "updated_at": "2026-09-01T00:00:00Z"}]
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: Response(url))
    records, configured = subscriber_records()
    assert configured and len(records) == 1
    assert records[0]["name"] is None and records[0]["status"] == "Active access"


@pytest.mark.parametrize("key,bearer", [
    ("sb_secret_test", False),
    ("legacy-service-role-jwt", True),
])
def test_subscriber_read_uses_correct_supabase_auth_header(monkeypatch, key, bearer):
    import requests
    monkeypatch.setenv("GETREEP_SUPABASE_URL", "https://ccitgqgjaktzpydqjulm.supabase.co")
    monkeypatch.setenv("GETREEP_SUPABASE_SERVICE_ROLE_KEY", key)
    captured = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return []
    def get(url, **kwargs):
        captured.append(kwargs["headers"])
        return Response()
    monkeypatch.setattr(requests, "get", get)
    assert subscriber_records() == ([], True)
    assert captured[0]["apikey"] == key
    assert ("Authorization" in captured[0]) is bearer


def test_subscriber_configuration_rejects_wrong_project_and_public_key(monkeypatch):
    monkeypatch.setenv("GETREEP_SUPABASE_URL", "https://other.supabase.co")
    monkeypatch.setenv("GETREEP_SUPABASE_SERVICE_ROLE_KEY", "sb_secret_test")
    with pytest.raises(SubscriberSourceError, match="wrong-project"):
        subscriber_records()
    monkeypatch.setenv("GETREEP_SUPABASE_URL", "https://ccitgqgjaktzpydqjulm.supabase.co")
    monkeypatch.setenv("GETREEP_SUPABASE_SERVICE_ROLE_KEY", "sb_publishable_test")
    with pytest.raises(SubscriberSourceError, match="wrong-key-role"):
        subscriber_records()
    assert supabase_key_role("sb_secret_test") == "service_role"


def test_live_totals_override_imports(app_module, client):
    authenticate(app_module, client)
    from models import InsightMetric
    day = date.today().isoformat()
    with app_module.app.app_context():
        for origin, value in [("manual", 100), ("apple", 7)]:
            app_module.db.session.add(InsightMetric(day=day, metric="first_downloads", source="Search", origin=origin, value=value, updated_at=day))
        app_module.db.session.commit()
    rows = client.get("/admin/getreep/api/dashboard").json["metrics"]
    assert len(rows) == 1 and rows[0]["value"] == 7


def apple_tsv(rows, dataset="downloads"):
    out = io.StringIO()
    columns = ["Date", "App Apple Identifier", "Counts", "Source Type"] + (["Download Type"] if dataset == "downloads" else ["Event", "Page Type"])
    writer = csv.writer(out, delimiter="\t")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue()


def test_apple_segments_aggregate_not_unique_counts():
    rows = [["2026-01-01", APP_ID, "4", "App Store search", "First-time Download"],
            ["2026-01-01", "other", "100", "App Store search", "First-time Download"]]
    result = aggregate_reports([apple_tsv(rows), apple_tsv(rows)], "downloads", "2026-01-03")
    assert result[0]["rows"][0]["value"] == 8
    assert result[0]["processingDate"] == "2026-01-03"


def test_apple_engagement_filters_page_types():
    rows = [["2026-01-01", APP_ID, "5", "App Store search", "Impression", "Search"],
            ["2026-01-01", APP_ID, "2", "App Store search", "Page view", "Product page"],
            ["2026-01-01", APP_ID, "50", "App Store search", "Page view", "In-app event"]]
    result = aggregate_reports([apple_tsv(rows, "engagement")], "engagement", "2026-01-03")
    assert {r["metric"]: r["value"] for r in result[0]["rows"]} == {"impressions": 5, "page_views": 2}


def test_unknown_apple_schema_fails():
    with pytest.raises(AppleReportError):
        aggregate_reports(["unexpected\tfields\n"], "downloads", "2026-01-01")


@pytest.mark.parametrize("url,allowed", [
    ("https://asp-prod-us-west-2.s3.us-west-2.amazonaws.com/reports/123/file.csv.gz?X-Amz-Signature=test", True),
    ("https://reports.apple.com/file.csv.gz", True),
    ("https://asp-prod-us-west-2.s3.us-west-2.amazonaws.com/other/file.csv.gz", False),
    ("https://asp-prod-us-west-2.s3.us-west-2.amazonaws.com.evil.example/reports/file.csv.gz", False),
    ("http://asp-prod-us-west-2.s3.us-west-2.amazonaws.com/reports/file.csv.gz", False),
    ("https://user@asp-prod-us-west-2.s3.us-west-2.amazonaws.com/reports/file.csv.gz", False),
])
def test_apple_segment_download_host_is_bounded(url, allowed):
    assert allowed_segment_url(url) is allowed


def test_apple_sync_explains_missing_generated_reports(monkeypatch):
    import apple_reports
    monkeypatch.setattr(apple_reports, "apple_token", lambda: "test-token")
    class Response:
        status_code = 200
        def __init__(self, path):
            self.path = path
        def json(self):
            if "analyticsReportRequests" in self.path and "/reports" not in self.path:
                return {"data": [{"id": "ongoing", "attributes": {"accessType": "ONGOING"}}]}
            return {"data": []}
    monkeypatch.setattr(apple_reports.requests, "request", lambda method, url, **kwargs: Response(url))
    batches, seen, message = sync_reports({})
    assert batches == [] and seen == []
    assert "downloads: report not generated" in message
    assert "engagement: report not generated" in message


def test_apple_sync_uses_existing_historical_snapshot(monkeypatch):
    import apple_reports
    monkeypatch.setattr(apple_reports, "apple_token", lambda: "test-token")
    day = date.today().isoformat()
    content = apple_tsv([[day, APP_ID, "3", "App Store search", "First-time Download"]]).encode()
    class ApiResponse:
        status_code = 200
        def __init__(self, url):
            self.url = url
        def json(self):
            if self.url.endswith(f"/apps/{APP_ID}/analyticsReportRequests"):
                return {"data": [{"id": "ongoing", "attributes": {"accessType": "ONGOING"}},
                                 {"id": "snapshot", "attributes": {"accessType": "ONE_TIME_SNAPSHOT"}}]}
            if "/snapshot/reports" in self.url:
                return {"data": [{"id": "downloads-report", "attributes": {"name": "App Downloads Standard"}}]}
            if "/downloads-report/instances" in self.url:
                return {"data": [{"id": "historical-instance", "attributes": {"processingDate": day}}]}
            if "/historical-instance/segments" in self.url:
                return {"data": [{"id": "segment", "attributes": {"url": "https://reports.apple.com/test"}}]}
            return {"data": []}
    class SegmentResponse:
        status_code = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def iter_content(self, size):
            yield content
    monkeypatch.setattr(apple_reports.requests, "request", lambda method, url, **kwargs: ApiResponse(url))
    monkeypatch.setattr(apple_reports.requests, "get", lambda url, **kwargs: SegmentResponse())
    batches, seen, message = sync_reports({})
    assert seen == ["historical-instance"]
    assert batches[0]["rows"][0]["value"] == 3
    assert "downloads: imported 1 dated rows" in message


def test_apple_corrections_do_not_regress(app_module, client, monkeypatch):
    authenticate(app_module, client)
    for key in ("ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_PRIVATE_KEY"):
        monkeypatch.setenv(key, "test-only")
    day = date.today().isoformat()
    def result(processing, value, identity):
        return ([dict(dataset="downloads", day=day, processingDate=processing,
                      rows=[dict(day=day, metric="first_downloads", source="Search", value=value)])],
                [identity], "Synced")
    for processing, value, identity in [("2026-02-03", 8, "new"), ("2026-02-01", 20, "old"), ("2026-02-04", 5, "correction")]:
        monkeypatch.setattr("apple_reports.sync_reports", lambda known, app_id, p=processing, v=value, i=identity: result(p, v, i))
        assert client.post("/admin/getreep/api/sync/apple").status_code == 200
        rows = client.get("/admin/getreep/api/dashboard").json["metrics"]
        assert sum(r["value"] for r in rows) == (5 if identity == "correction" else 8)


def test_subscriber_failure_does_not_expose_details(app_module, client, monkeypatch):
    authenticate(app_module, client)
    monkeypatch.setattr("insights.subscriber_records", lambda: (_ for _ in ()).throw(ValueError("secret-credential")))
    response = client.get("/admin/getreep/api/dashboard")
    assert response.status_code == 200
    assert response.json["subscriberError"]
    assert b"secret-credential" not in response.data
    assert response.json["connections"]["subscribers"]["lastSync"] is None


def test_successful_empty_subscriber_check_has_timestamp(app_module, client, monkeypatch):
    authenticate(app_module, client)
    monkeypatch.setattr("insights.subscriber_records", lambda: ([], True))
    body = client.get("/admin/getreep/api/dashboard").json
    assert body["subscribers"] == [] and body["subscriberError"] is None
    assert body["connections"]["subscribers"]["configured"] is True
    assert body["subscriberFetchedAt"]
    assert body["connections"]["subscribers"]["lastSync"] == body["subscriberFetchedAt"]


def test_portfolio_imports_and_corrections_stay_separate(app_module, client, monkeypatch):
    authenticate(app_module, client)
    apps = [{"id": APP_ID, "name": "Getreep"}, {"id": "6790227598", "name": "VocalFrame"}]
    monkeypatch.setattr("apple_reports.list_apps", lambda: apps)
    assert client.post("/admin/getreep/api/sync/apps").json["apps"] == apps
    day = date.today().isoformat()
    for app, value in zip(apps, [5, 9]):
        payload = f"day,metric,source,value\n{day},first_downloads,Search,{value}\n"
        assert client.post("/admin/getreep/api/import", data=payload, headers={"X-Insights-App": app["id"]}).status_code == 200
    for key in ("ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_PRIVATE_KEY"):
        monkeypatch.setenv(key, "test-only")
    def correction(known, app_id):
        assert app_id == APP_ID
        return ([dict(dataset="downloads", day=day, processingDate=day, rows=[])], ["empty-correction"], "Corrected")
    monkeypatch.setattr("apple_reports.sync_reports", correction)
    assert client.post("/admin/getreep/api/sync/apple", headers={"X-Insights-App": APP_ID}).status_code == 200
    result = client.get("/admin/getreep/api/dashboard").json
    assert len(result["apps"]) == 4
    assert [(r["appId"], r["value"]) for r in result["metrics"]] == [("6790227598", 9)]
    assert result["apps"][0]["lastSync"] is not None
    assert result["apps"][1]["lastSync"] is None
    assert client.post("/admin/getreep/api/sync/apple", headers={"X-Insights-App": "../../invalid"}).status_code == 400


def test_unknown_app_import_rejected_and_catalog_failure_keeps_apps(app_module, client, monkeypatch):
    authenticate(app_module, client)
    payload = f"day,metric,source,value\n{date.today().isoformat()},first_downloads,Search,3\n"
    assert client.post("/admin/getreep/api/import", data=payload, headers={"X-Insights-App": "all"}).status_code == 400
    monkeypatch.setattr("apple_reports.list_apps", lambda: (_ for _ in ()).throw(AppleReportError("secret")))
    failure = client.post("/admin/getreep/api/sync/apps")
    assert failure.status_code == 502 and b"secret" not in failure.data
    assert client.get("/admin/getreep/api/dashboard").json["apps"][0]["id"] == APP_ID


def test_apple_parser_targets_selected_app():
    rows = [["2026-01-01", APP_ID, "4", "Search", "First-time Download"],
            ["2026-01-01", "6790227598", "9", "Search", "First-time Download"]]
    result = aggregate_reports([apple_tsv(rows)], "downloads", "2026-01-03", "6790227598")
    assert result[0]["rows"][0]["value"] == 9


def test_website_activity_has_app_attribution(app_module, client, monkeypatch):
    monkeypatch.setenv("INSIGHTS_TRACKING_ENABLED", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    headers = {"User-Agent": "Mozilla/5.0"}
    client.get("/getreep", headers=headers)
    client.get("/vocalframe", headers=headers)
    client.get("/", headers=headers)
    response = client.get("/go/vocalframe?next=https://untrusted.example", headers=headers)
    assert response.location == app_module.VOCALFRAME_APP_STORE_URL
    authenticate(app_module, client)
    rows = client.get("/admin/getreep/api/dashboard").json["metrics"]
    assert {r["source"]: r["appId"] for r in rows} == {
        "/getreep": APP_ID, "/vocalframe": "6790227598", "/": "website", "/go/vocalframe": "6790227598"}
