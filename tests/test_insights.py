import csv
import io
from datetime import date
import pytest
from test_app import app_module, client, create_user, login
from insights import parse_import
from apple_reports import aggregate_reports, AppleReportError, APP_ID


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
    assert b"Getreep Insights" in client.get("/admin").data
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
        assert reporting == {"insight_metrics", "insight_state"}
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
        monkeypatch.setattr("apple_reports.sync_reports", lambda known, p=processing, v=value, i=identity: result(p, v, i))
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
