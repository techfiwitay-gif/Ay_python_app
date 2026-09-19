"""Private Getreep reporting attached to the existing AyNcode administrator."""
import csv
import io
import os
import re
from datetime import datetime, timedelta, timezone, date
from functools import wraps
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf.csrf import generate_csrf, validate_csrf
from wtforms.validators import ValidationError

APPLE_METRICS = {"first_downloads", "redownloads", "impressions", "page_views"}
METRICS = APPLE_METRICS | {"website_page_views", "download_clicks"}
GETREEP_ID = "6799787039"
VOCALFRAME_ID = "6790227598"


def now():
    return datetime.now(timezone.utc).isoformat()


def storage_failure_kind(error):
    """Allowlisted diagnostics only: never log DSNs, SQL parameters or secrets."""
    message = str(getattr(error, "orig", error)).lower()
    for needle, category in (
        ("unsupported startup parameter", "unsupported-startup-parameter"),
        ("invalid connection option", "invalid-connection-option"),
        ("password authentication failed", "authentication-failed"),
        ("does not exist", "missing-schema-or-resource"),
        ("timeout", "connection-or-query-timeout"),
        ("could not translate host", "dns-failed"),
        ("ssl", "tls-connection-failed"),
        ("permission denied", "permission-denied"),
    ):
        if needle in message:
            return category
    return "unclassified-storage-error"


def parse_import(raw):
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")))
    if reader.fieldnames != ["day", "metric", "source", "value"]:
        raise ValueError("Use the template columns: day,metric,source,value.")
    rows, seen = [], set()
    for row in reader:
        if len(rows) >= 3000 or None in row or any(v is None for v in row.values()):
            raise ValueError("Invalid CSV row or more than 3,000 measurements.")
        day, metric, source, value = (row[k].strip() for k in reader.fieldnames)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or date.fromisoformat(day) > date.today():
            raise ValueError("Use valid dates that are not in the future.")
        if metric not in METRICS or not source or len(source) > 120 or any(ord(c) < 32 for c in source):
            raise ValueError("Invalid metric or source.")
        if not re.fullmatch(r"\d{1,12}", value):
            raise ValueError("Counts must be non-negative whole numbers.")
        key = (day, metric, source)
        if key in seen:
            raise ValueError("Duplicate date, metric, and source in the CSV.")
        seen.add(key)
        rows.append(dict(day=day, metric=metric, source=source, value=int(value)))
    if not rows:
        raise ValueError("The CSV contains no measurements.")
    return rows


def subscriber_records():
    """Service key never leaves the server. No auth-user/email/trip queries."""
    base = os.environ.get("GETREEP_SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("GETREEP_SUPABASE_SERVICE_ROLE_KEY", "")
    if not base or not key:
        return [], False
    parsed = urlparse(base)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".supabase.co") or parsed.path:
        raise ValueError("Invalid subscription service configuration.")
    headers = {"apikey": key, "Authorization": "Bearer " + key}
    def read(table, params):
        response = requests.get(base + "/rest/v1/" + table, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, list):
            raise ValueError("Invalid subscription response.")
        return result
    rows = []
    for offset in range(0, 10001, 500):
        batch = read("subscription_entitlements", {
            "select": "user_id,product_id,environment,expires_at,updated_at",
            "entitlement": "eq.getreep_plus", "order": "user_id", "limit": 500, "offset": offset})
        rows.extend(batch)
        if len(rows) > 10000:
            raise ValueError("Subscriber list exceeds the supported page limit.")
        if len(batch) < 500:
            break
    names = {}
    for offset in range(0, len(rows), 100):
        ids = [r["user_id"] for r in rows[offset:offset+100]]
        if any(not re.fullmatch(r"[a-fA-F0-9-]{36}", uid) for uid in ids):
            raise ValueError("Invalid subscriber account identifier.")
        for profile in read("profiles", {"select": "user_id,name", "user_id": "in.(" + ",".join(ids) + ")"}):
            names[profile["user_id"]] = profile.get("name")
    result = []
    for row in rows:
        expiration = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        env = (row.get("environment") or "").upper()
        result.append(dict(id=row["user_id"], name=names.get(row["user_id"]),
                           product=row.get("product_id") or "Getreep Plus",
                           environment=env if env in {"PRODUCTION", "SANDBOX"} else "UNKNOWN",
                           status="Active access" if expiration > datetime.now(timezone.utc) else "Expired access",
                           expiresAt=row["expires_at"], updatedAt=row["updated_at"]))
    return result, True


def register_insights(app, db, is_admin, store_url):
    # Imports inside registration support the application's isolated test fixtures.
    from models import InsightMetric, AppInsightMetric, InsightState
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.exc import SQLAlchemyError

    bp = Blueprint("insights", __name__)
    def protected(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not is_admin(current_user):
                if request.path.endswith("/api/dashboard") or "/api/" in request.path:
                    return jsonify(error="Sign in with your AyNcode administrator account."), 401 if not current_user.is_authenticated else 403
                if not current_user.is_authenticated:
                    return redirect(url_for("admin", next="/admin/getreep"))
                return "Administrator access required.", 403
            if request.method == "POST" and app.config.get("WTF_CSRF_ENABLED", True):
                try:
                    validate_csrf(request.headers.get("X-CSRFToken"))
                except ValidationError:
                    return jsonify(error="Your session expired. Reload the page and try again."), 400
                origin = request.headers.get("Origin")
                if origin and origin != request.host_url.rstrip("/"):
                    return jsonify(error="Invalid request origin."), 403
            try:
                if "/api/" in request.path and os.environ.get("VERCEL") and not os.environ.get("INSIGHTS_DATABASE_URL"):
                    return jsonify(error="Persistent reporting storage is not configured."), 503
                return fn(*args, **kwargs)
            except SQLAlchemyError as error:
                db.session.rollback()
                app.logger.error("Insights storage unavailable: %s", storage_failure_kind(error))
                return jsonify(error="Reporting storage is temporarily unavailable. Please try again shortly."), 503
        return wrapped

    def state(key, value):
        db.session.merge(InsightState(key=key, value=value, updated_at=now()))

    def app_catalog():
        catalog = db.session.get(InsightState, "apple-apps")
        return catalog.value["apps"] if catalog else [{"id": GETREEP_ID, "name": "Getreep"}]

    def selected_app():
        identity = request.headers.get("X-Insights-App", GETREEP_ID)
        if identity not in {a["id"] for a in app_catalog()}:
            raise ValueError("Choose an app from the connected Apple account.")
        return identity

    def tracking_enabled():
        # Vercel's temporary SQLite filesystem must never masquerade as durable reporting.
        return os.environ.get("INSIGHTS_TRACKING_ENABLED") == "1" and (
            not os.environ.get("VERCEL") or
            db.engines["insights"].dialect.name == "postgresql")

    @bp.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Vary"] = "Cookie"
        return response

    @bp.get("/admin/getreep")
    @protected
    def dashboard():
        return render_template("getreep_insights.html", csrf_token=generate_csrf())

    @bp.get("/admin/getreep/metrics-template.csv")
    @protected
    def template():
        return app.response_class("day,metric,source,value\n", mimetype="text/csv",
                                  headers={"Content-Disposition": "attachment; filename=metrics-template.csv"})

    @bp.get("/admin/getreep/api/dashboard")
    @protected
    def data():
        cutoff = (date.today() - timedelta(days=90)).isoformat()
        records = InsightMetric.query.filter(InsightMetric.day >= cutoff).all()
        records += AppInsightMetric.query.filter(AppInsightMetric.day >= cutoff).all()
        def identity(row):
            if hasattr(row, "app_id"):
                return row.app_id
            if row.origin != "website":
                return GETREEP_ID  # Preserve imports created before portfolio support.
            return {"/getreep": GETREEP_ID, "/go/getreep": GETREEP_ID,
                    "/vocalframe": VOCALFRAME_ID, "/go/vocalframe": VOCALFRAME_ID}.get(row.source, "website")
        # Live reports replace imported totals for that metric/day, never double-count.
        priority = {"manual": 0, "website": 1, "apple": 2}
        chosen = {}
        for row in records:
            key = (identity(row), row.day, row.metric)
            chosen[key] = max(chosen.get(key, 0), priority[row.origin])
        # An empty corrected Apple batch is still authoritative; do not revive old imports.
        for batch in InsightState.query.filter(InsightState.key.like("apple:%")).all():
            parts = batch.key.split(":")
            if len(parts) == 3:
                parts.insert(1, GETREEP_ID)
            if len(parts) == 4 and parts[2] in {"downloads", "engagement"}:
                family = {"first_downloads", "redownloads"} if parts[2] == "downloads" else {"impressions", "page_views"}
                for metric in family:
                    chosen[(parts[1], parts[3], metric)] = 2
        metrics = [dict(appId=identity(r), day=r.day, metric=r.metric, source=r.source, value=r.value)
                   for r in records if priority[r.origin] == chosen[(identity(r), r.day, r.metric)]]
        apple_state = db.session.get(InsightState, "apple-sync")
        web_last = max((r.updated_at for r in records if r.origin == "website"), default=None)
        apple_ready = all(os.environ.get(k) for k in ("ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_PRIVATE_KEY"))
        subscribers, configured, error = [], False, None
        try:
            subscribers, configured = subscriber_records()
        except (requests.RequestException, ValueError, KeyError, TypeError):
            configured = bool(os.environ.get("GETREEP_SUPABASE_SERVICE_ROLE_KEY"))
            error = "Subscription records are temporarily unavailable. Check the server connection."
        apps = app_catalog()
        statuses = {a["id"]: db.session.get(InsightState, "apple-sync:" + a["id"]) for a in apps}
        return jsonify(apps=[dict(**a, lastSync=statuses[a["id"]].updated_at if statuses[a["id"]] else None,
                                  message=statuses[a["id"]].value.get("message") if statuses[a["id"]] else "Not synced yet") for a in apps],
                       metrics=metrics, subscribers=[dict(**s, appId=GETREEP_ID) for s in subscribers], subscriberError=error,
                       subscriberFetchedAt=now() if configured and not error else None,
                       connections={
                           "apple": dict(configured=apple_ready, lastSync=apple_state.updated_at if apple_state else None,
                                         message=apple_state.value.get("message", "") if apple_state else "Sync Apple reports to discover all apps." if apple_ready else "Add server-only Apple reporting credentials to connect."),
                           "subscribers": dict(configured=configured, lastSync=None,
                                               message="Read-only linked account access." if configured else "Server-only Getreep subscription connection is needed."),
                           "website": dict(configured=tracking_enabled(), lastSync=web_last,
                                           message="Aggregate page views and app-specific link clicks; bots filtered where recognizable. Not unique visitors." if tracking_enabled() else "Website counting is prepared but not enabled. Enable it with a persistent database.")})

    @bp.post("/admin/getreep/api/import")
    @protected
    def import_csv():
        if request.content_length is not None and request.content_length > 2_000_000:
            return jsonify(error="Please use a CSV under 2 MB."), 413
        raw = request.stream.read(2_000_001)
        if len(raw) > 2_000_000:
            return jsonify(error="Please use a CSV under 2 MB."), 413
        try:
            rows = parse_import(raw.decode("utf-8-sig"))
            app_id = selected_app()
        except (ValueError, UnicodeError, csv.Error):
            return jsonify(error="Invalid CSV. Use the template, valid past dates, supported metrics, and whole-number counts."), 400
        try:
            for row in rows:
                if app_id == GETREEP_ID:
                    InsightMetric.query.filter_by(day=row["day"], metric=row["metric"], source=row["source"], origin="manual").delete()
                db.session.merge(AppInsightMetric(app_id=app_id, **row, origin="manual", updated_at=now()))
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.error("Insights import storage failed")
            return jsonify(error="Could not save the import. Previous reports are unchanged."), 503
        return jsonify(rows=len(rows))

    @bp.post("/admin/getreep/api/sync/apps")
    @protected
    def sync_apps():
        from apple_reports import list_apps, AppleReportError
        try:
            apps = list_apps()
            state("apple-apps", {"apps": apps})
            db.session.commit()
            return jsonify(apps=apps)
        except (AppleReportError, requests.RequestException, ValueError, KeyError):
            return jsonify(error="Could not refresh the Apple app list. Saved apps are unchanged."), 502

    @bp.post("/admin/getreep/api/sync/apple")
    @protected
    def sync_apple():
        from apple_reports import sync_reports, AppleReportError
        from sqlalchemy.exc import IntegrityError
        if not all(os.environ.get(k) for k in ("ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_PRIVATE_KEY")):
            return jsonify(error="Add Apple reporting credentials in the server settings first."), 409
        try:
            app_id = selected_app()
        except ValueError:
            return jsonify(error="Choose a connected app before syncing."), 400
        prefix = "apple:" + app_id + ":"
        lock_key = "apple-lock:" + app_id
        # Cross-worker lease; concurrent clicks cannot apply competing corrections.
        lease = now()
        stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        InsightState.query.filter(InsightState.key == lock_key, InsightState.updated_at < stale).delete()
        try:
            db.session.add(InsightState(key=lock_key, value={}, updated_at=lease))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="Apple reports are already syncing. Try refreshing shortly."), 409
        try:
            known = {"apple:" + s.key[len(prefix):]: s.value for s in InsightState.query.filter(InsightState.key.like(prefix + "%")).all()}
            batches, seen, message = sync_reports(known, app_id)
            for batch in batches:
                key = "apple:" + batch["dataset"] + ":" + batch["day"]
                previous = known.get(key, {}).get("processingDate", "")
                if previous > batch["processingDate"]:
                    continue
                family = {"first_downloads", "redownloads"} if batch["dataset"] == "downloads" else {"impressions", "page_views"}
                AppInsightMetric.query.filter(AppInsightMetric.app_id == app_id, AppInsightMetric.day == batch["day"], AppInsightMetric.origin == "apple",
                                              AppInsightMetric.metric.in_(family)).delete(synchronize_session=False)
                if app_id == GETREEP_ID:
                    InsightMetric.query.filter(InsightMetric.day == batch["day"], InsightMetric.origin == "apple", InsightMetric.metric.in_(family)).delete(synchronize_session=False)
                for row in batch["rows"]:
                    db.session.merge(AppInsightMetric(app_id=app_id, **row, origin="apple", updated_at=now()))
                state(prefix + batch["dataset"] + ":" + batch["day"], {"processingDate": batch["processingDate"]})
                known[key] = {"processingDate": batch["processingDate"]}
            for instance in seen:
                state(prefix + "instance:" + instance, {})
            state("apple-sync:" + app_id, {"message": message})
            state("apple-sync", {"message": message})
            db.session.commit()
            return jsonify(message=message)
        except (AppleReportError, requests.RequestException, ValueError, KeyError, OSError):
            db.session.rollback()
            return jsonify(error="Apple reports could not be synced. Check key permissions and try again; saved reports are unchanged."), 502
        finally:
            InsightState.query.filter_by(key=lock_key, updated_at=lease).delete()
            db.session.commit()

    app.register_blueprint(bp)

    @app.get("/go/getreep")
    def getreep_download():
        return redirect(store_url, code=302)

    @app.get("/go/vocalframe")
    def vocalframe_download():
        return redirect("https://apps.apple.com/app/vocalframe-camera-coach/id6790227598", code=302)

    @app.after_request
    def count_public_activity(response):
        if not tracking_enabled() or request.method != "GET" or is_admin(current_user):
            return response
        if request.headers.get("DNT") == "1" or request.headers.get("Sec-GPC") == "1":
            return response
        ua = request.headers.get("User-Agent", "").lower()
        if not ua or any(word in ua for word in ("bot", "spider", "crawler", "headless", "preview")):
            return response
        if "prefetch" in (request.headers.get("Purpose", "") + request.headers.get("Sec-Purpose", "")).lower():
            return response
        # Only named public landing pages; never store arbitrary URLs/query strings.
        metric = "download_clicks" if request.path in {"/go/getreep", "/go/vocalframe"} and response.status_code == 302 else None
        if request.path in {"/", "/products", "/getreep", "/about", "/contact", "/vocalframe"} and response.status_code == 200:
            metric = "website_page_views"
        if not metric:
            return response
        try:
            insert = pg_insert if db.engines["insights"].dialect.name == "postgresql" else sqlite_insert
            stmt = insert(InsightMetric).values(day=date.today().isoformat(), metric=metric, source=request.path,
                                                origin="website", value=1, updated_at=now())
            db.session.execute(stmt.on_conflict_do_update(
                index_elements=["day", "metric", "source", "origin"],
                set_={"value": InsightMetric.value + 1, "updated_at": now()}))
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.warning("Insights aggregate counter unavailable")
        return response
