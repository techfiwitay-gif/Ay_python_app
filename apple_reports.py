"""Bounded, on-demand App Store Connect Analytics Reports reader."""
import csv
import gzip
import io
import os
import re
import time
from collections import defaultdict
from datetime import date
from urllib.parse import urlparse

import jwt
import requests

APP_ID = "6799787039"
API = "https://api.appstoreconnect.apple.com"


class AppleReportError(ValueError):
    pass


def allowed_segment_url(url):
    """Allow Apple's signed report files, never arbitrary hosts or redirects."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    apple_host = host.endswith((".apple.com", ".mzstatic.com"))
    # Apple's API examples use a bucket-specific S3 URL for report segments.
    s3_host = bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com", host))
    return apple_host or (s3_host and parsed.path.startswith("/reports/"))


def aggregate_reports(texts, dataset, processing_date, app_id=APP_ID):
    totals = defaultdict(int)
    days = set()
    for content in texts:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), delimiter="\t")
        required = {"Date", "App Apple Identifier", "Counts"}
        required |= {"Download Type"} if dataset == "downloads" else {"Event", "Page Type"}
        if not required.issubset(reader.fieldnames or []):
            raise AppleReportError("Unrecognized Apple report columns.")
        for row in reader:
            if row.get("App Apple Identifier") != app_id:
                continue
            day = row["Date"]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or date.fromisoformat(day) > date.today():
                raise AppleReportError("Invalid report date.")
            days.add(day)
            if dataset == "downloads":
                metric = {"first-time download": "first_downloads", "redownload": "redownloads"}.get(row["Download Type"].lower())
            else:
                event = row["Event"].lower()
                metric = "impressions" if event == "impression" else "page_views" if event == "page view" and row["Page Type"].lower() == "product page" else None
            count = row.get("Counts", "").strip()
            if not metric or count in {"", "-", "*"}:
                continue
            if not re.fullmatch(r"\d{1,12}", count):
                raise AppleReportError("Invalid report count.")
            source = row.get("Source Type", "").strip() or "Unspecified"
            if len(source) > 120:
                raise AppleReportError("Invalid report source.")
            totals[(day, metric, source)] += int(count)
    return [dict(dataset=dataset, day=day, processingDate=processing_date,
                 rows=[dict(day=d, metric=m, source=s, value=n)
                       for (d, m, s), n in totals.items() if d == day]) for day in sorted(days)]


def apple_token():
    try:
        return jwt.encode({"iss": os.environ["ASC_ISSUER_ID"], "iat": int(time.time()),
                            "exp": int(time.time()) + 600, "aud": "appstoreconnect-v1"},
                           os.environ["ASC_PRIVATE_KEY"].replace("\\n", "\n"), algorithm="ES256",
                           headers={"kid": os.environ["ASC_KEY_ID"], "typ": "JWT"})
    except Exception as exc:
        raise AppleReportError("Invalid reporting credentials.") from exc


def list_apps():
    """Discover the authorized team's apps without hardcoding a portfolio."""
    token = apple_token()
    url = API + "/v1/apps?fields[apps]=name,bundleId&limit=200"
    result = []
    for _ in range(5):
        if urlparse(url).netloc != "api.appstoreconnect.apple.com" or not url.startswith("https://"):
            raise AppleReportError("Invalid Apple pagination URL.")
        response = requests.get(url, headers={"Authorization": "Bearer " + token}, timeout=8, allow_redirects=False)
        if response.status_code != 200:
            raise AppleReportError("Could not load the Apple app list.")
        page = response.json()
        for row in page["data"]:
            if not re.fullmatch(r"\d{1,30}", row["id"]):
                raise AppleReportError("Invalid Apple app identifier.")
            result.append(dict(id=row["id"], name=row["attributes"]["name"], bundleId=row["attributes"].get("bundleId", "")))
        url = page.get("links", {}).get("next")
        if not url:
            return result
    raise AppleReportError("Apple app-list pagination limit reached.")


def sync_reports(known, app_id=APP_ID):
    if not re.fullmatch(r"\d{1,30}", app_id):
        raise AppleReportError("Invalid Apple app identifier.")
    started = time.monotonic()
    token = apple_token()
    def budget():
        if time.monotonic() - started > 40:
            raise AppleReportError("Report sync timed out. Retry later.")
    def api(path, method="GET", payload=None):
        budget()
        url = path if path.startswith("https://") else API + path
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.appstoreconnect.apple.com":
            raise AppleReportError("Invalid Apple pagination URL.")
        r = requests.request(method, url, json=payload, headers={"Authorization": "Bearer " + token},
                             timeout=8, allow_redirects=False)
        if r.status_code >= 300:
            raise AppleReportError(f"Apple reporting API returned HTTP {r.status_code}.")
        return r.json()
    def listing(path):
        rows = []
        for _ in range(5):
            page = api(path)
            rows.extend(page["data"])
            path = page.get("links", {}).get("next")
            if not path:
                return rows
        raise AppleReportError("Apple pagination limit reached.")
    requests_list = listing(f"/v1/apps/{app_id}/analyticsReportRequests")
    ongoing = next((r for r in requests_list if r["attributes"].get("accessType") == "ONGOING"
                    and not r["attributes"].get("stoppedDueToInactivity")), None)
    if not ongoing:
        api("/v1/analyticsReportRequests", "POST", {"data": {"type": "analyticsReportRequests",
            "attributes": {"accessType": "ONGOING"}, "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}}})
        return [], [], "Apple reporting requested. First reports can take 24–48 hours; return here and sync again."
    # An Admin may have requested a one-time snapshot to supply history. Read it
    # after the ongoing feed without creating any new privileged Apple request.
    request_rows = [ongoing] + [r for r in requests_list if r["attributes"].get("accessType") == "ONE_TIME_SNAPSHOT"]
    report_sets = [listing(f"/v1/analyticsReportRequests/{row['id']}/reports?limit=200") for row in request_rows]
    batches, seen, statuses = [], [], []
    for dataset, titles in (("downloads", ("App Store Downloads", "App Downloads")),
                            ("engagement", ("App Store Discovery and Engagement",))):
        accepted = {name for title in titles for name in (title, title + " Standard")}
        candidates = [next((r for r in reports if r["attributes"].get("name") in accepted), None)
                      for reports in report_sets]
        candidates = [r for r in candidates if r]
        if not candidates:
            statuses.append(f"{dataset}: report not generated")
            continue
        instance = None
        for report in candidates:
            instances = listing(f"/v1/analyticsReports/{report['id']}/instances?filter[granularity]=DAILY&limit=30")
            instances.sort(key=lambda r: r["attributes"]["processingDate"], reverse=True)
            pending = [i for i in instances if "apple:instance:" + i["id"] not in known]
            if pending:
                instance = pending[0]
                break
        if not instance:
            statuses.append(f"{dataset}: no new daily instance")
            continue
        # One full instance per dataset per click; every segment must succeed.
        segments = listing(f"/v1/analyticsReportInstances/{instance['id']}/segments?limit=200")
        if not segments:
            statuses.append(f"{dataset}: instance has no segments")
            continue
        texts = []
        if len(segments) > 10:
            raise AppleReportError("Report requires a larger background worker.")
        for segment in segments:
            budget()
            url = segment["attributes"]["url"]
            if not allowed_segment_url(url):
                raise AppleReportError("Unexpected Apple report download host.")
            with requests.get(url, timeout=8, stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise AppleReportError("Report download failed.")
                parts, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 25_000_000:
                        raise AppleReportError("Report is too large.")
                    parts.append(chunk)
            raw = b"".join(parts)
            if raw[:2] == b"\x1f\x8b":
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                    raw = zipped.read(25_000_001)
            if len(raw) > 25_000_000:
                raise AppleReportError("Expanded report is too large.")
            texts.append(raw.decode("utf-8-sig"))
        batches.extend(aggregate_reports(texts, dataset, instance["attributes"]["processingDate"], app_id))
        seen.append(instance["id"])
        statuses.append(f"{dataset}: imported {sum(batch['dataset'] == dataset for batch in batches)} dated rows")
    return batches, seen, ("Synced Apple report batches. Sync again to check for older reports. " if seen
                            else "No new Apple report batches. ") + "; ".join(statuses) + ". Counts are delayed and may be privacy-limited."
