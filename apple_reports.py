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


def aggregate_reports(texts, dataset, processing_date):
    totals = defaultdict(int)
    days = set()
    for content in texts:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), delimiter="\t")
        required = {"Date", "App Apple Identifier", "Counts"}
        required |= {"Download Type"} if dataset == "downloads" else {"Event", "Page Type"}
        if not required.issubset(reader.fieldnames or []):
            raise AppleReportError("Unrecognized Apple report columns.")
        for row in reader:
            if row.get("App Apple Identifier") != APP_ID:
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


def sync_reports(known):
    started = time.monotonic()
    try:
        token = jwt.encode({"iss": os.environ["ASC_ISSUER_ID"], "iat": int(time.time()),
                            "exp": int(time.time()) + 600, "aud": "appstoreconnect-v1"},
                           os.environ["ASC_PRIVATE_KEY"].replace("\\n", "\n"), algorithm="ES256",
                           headers={"kid": os.environ["ASC_KEY_ID"], "typ": "JWT"})
    except Exception as exc:
        raise AppleReportError("Invalid reporting credentials.") from exc
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
            raise AppleReportError("Apple API request failed.")
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
    requests_list = listing(f"/v1/apps/{APP_ID}/analyticsReportRequests")
    ongoing = next((r for r in requests_list if r["attributes"].get("accessType") == "ONGOING"
                    and not r["attributes"].get("stoppedDueToInactivity")), None)
    if not ongoing:
        api("/v1/analyticsReportRequests", "POST", {"data": {"type": "analyticsReportRequests",
            "attributes": {"accessType": "ONGOING"}, "relationships": {"app": {"data": {"type": "apps", "id": APP_ID}}}}})
        return [], [], "Apple reporting requested. First reports can take 24–48 hours; return here and sync again."
    reports = listing(f"/v1/analyticsReportRequests/{ongoing['id']}/reports?limit=200")
    batches, seen = [], []
    for dataset, title in (("downloads", "App Store Downloads"), ("engagement", "App Store Discovery and Engagement")):
        report = next((r for r in reports if r["attributes"].get("name") in {title, title + " Standard"}), None)
        if not report:
            continue
        instances = listing(f"/v1/analyticsReports/{report['id']}/instances?filter[granularity]=DAILY&limit=30")
        instances.sort(key=lambda r: r["attributes"]["processingDate"], reverse=True)
        pending = [i for i in instances if "apple:instance:" + i["id"] not in known]
        # One full instance per report per click; every segment must succeed before saving.
        if not pending:
            continue
        instance = pending[0]
        segments = listing(f"/v1/analyticsReportInstances/{instance['id']}/segments?limit=200")
        if not segments:
            continue
        texts = []
        if len(segments) > 10:
            raise AppleReportError("Report requires a larger background worker.")
        for segment in segments:
            budget()
            url = segment["attributes"]["url"]
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.port not in (None, 443) or not (parsed.hostname or "").endswith((".apple.com", ".mzstatic.com")):
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
        batches.extend(aggregate_reports(texts, dataset, instance["attributes"]["processingDate"]))
        seen.append(instance["id"])
    return batches, seen, ("Synced Apple report batches. Sync again to check for older reports. Counts are delayed and may be privacy-limited."
                            if seen else "No new Apple report batches are available yet. Reports are delayed and privacy-limited.")
