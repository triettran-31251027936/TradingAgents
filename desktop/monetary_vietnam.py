"""Dated NHNN briefing facts published by Deposit Insurance of Vietnam.

This is a release adapter, not a daily policy-rate series or a vintage archive.
Missing or changed prose never becomes a guessed number.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from desktop.vietnam import TZ, write_json

ROOT = "https://div.gov.vn/"
SEED = (
    ROOT
    + "ngan-hang-nha-nuoc-hop-bao-ve-ket-qua-dieu-hanh-chinh-sach-tien-te-va-hoat-dong-ngan-hang-9-thang-thuc-day-ngan-hang-so-bao-dam-quyen-loi-hop-phap-cua-nguoi-gui-tien"
)
UNITS = {
    "vn_credit_stock": "VND",
    "vn_credit_ytd": "fraction",
    "vn_credit_yoy": "fraction",
    "vn_new_deposit_rate": "fraction/year",
}
MAX_BYTES = 2_000_000


def trusted(url):
    p = urlparse(url)
    return p.scheme == "https" and p.netloc == "div.gov.vn" and not p.query and not p.fragment


def parse(raw, url, observed):
    if observed.tzinfo is None:
        raise ValueError("Monetary observation requires timezone")
    if not trusted(url) or len(raw.encode()) > MAX_BYTES:
        raise ValueError("Untrusted or oversized monetary release")
    soup = BeautifulSoup(raw, "html.parser")
    body, stamp, title = (
        soup.select_one(".entry-content"),
        soup.select_one(".date_post_core"),
        soup.find("h1"),
    )
    if body is None or stamp is None or title is None:
        raise ValueError("Monetary release schema changed")
    published = re.search(r"\b(\d{2})/(\d{2})/(20\d{2})\b", stamp.get_text())
    if (
        not published
        or "ngân hàng nhà nước" not in title.get_text().lower()
        or "họp báo" not in title.get_text().lower()
    ):
        raise ValueError("Not a dated NHNN briefing")
    published = date(int(published[3]), int(published[2]), int(published[1]))
    if published > observed.date():
        raise ValueError("Future monetary release")
    text = " ".join(body.get_text(" ", strip=True).split())
    digest = hashlib.sha256(raw.encode()).hexdigest()
    candidates = {k: [] for k in UNITS}

    def add(alias, value, period):
        if period > published:
            return
        candidates[alias].append((value, period.isoformat()))

    def projected(match):
        start = text.rfind(". ", 0, match.start()) + 2
        return bool(
            re.search(
                r"dự kiến|mục tiêu|phấn đấu|kỳ vọng|dự báo",
                text[max(0, start - 2) : match.start()],
                re.I,
            )
        )

    number = r"\d+(?:,\d+)?"
    # Fully dated subject and explicit comparison years are required. Do not
    # pick up an individual bank, a credit programme, or a target for next year.
    pattern = rf"đến ngày (\d{{1,2}})/(\d{{1,2}})/(20\d{{2}}), dư nợ tín dụng toàn hệ thống đạt ({number}) triệu tỷ đồng, tăng ({number})% so với cuối năm (20\d{{2}}) và tăng ({number})% so với cùng kỳ năm (20\d{{2}})"
    for m in re.finditer(pattern, text, re.I):
        if projected(m):
            continue
        period = date(int(m[3]), int(m[2]), int(m[1]))
        if int(m[6]) != period.year - 1 or int(m[8]) != period.year - 1:
            continue
        add("vn_credit_stock", float(m[4].replace(",", ".")) * 1e15, period)
        add("vn_credit_ytd", float(m[5].replace(",", ".")) / 100, period)
        add("vn_credit_yoy", float(m[7].replace(",", ".")) / 100, period)
    pattern = rf"Đến ngày (\d{{1,2}})/(\d{{1,2}})/(20\d{{2}}), lãi suất tiền gửi bình quân của các giao dịch phát sinh mới ở mức ({number})%/năm"
    for m in re.finditer(pattern, text, re.I):
        if projected(m):
            continue
        add(
            "vn_new_deposit_rate",
            float(m[4].replace(",", ".")) / 100,
            date(int(m[3]), int(m[2]), int(m[1])),
        )
    metrics, conflicts = {}, []
    for alias, rows in candidates.items():
        if not rows:
            continue
        periods = sorted({p for _, p in rows})
        latest = periods[-1]
        values = {v for v, p in rows if p == latest}
        if len(values) != 1:
            conflicts.append(alias)
            continue
        value = values.pop()
        if value < 0 or (alias != "vn_credit_stock" and value > 1):
            continue
        metrics[alias] = {
            "value": value,
            "unit": UNITS[alias],
            "asof": latest,
            "source": url,
            "published": published.isoformat(),
            "observed_at": observed.isoformat(),
            "source_sha256": digest,
            "status": "available",
            "formula": "Exact dated NHNN briefing fact published by DIV; percent /100 or million-billion VND *1e15; new-deposit average is NOT a policy rate",
        }
    return {
        "url": url,
        "title": title.get_text(" ", strip=True),
        "published": published.isoformat(),
        "observed_at": observed.isoformat(),
        "sha256": digest,
        "metrics": metrics,
        "conflicts": conflicts,
    }


def load(asof, cache_dir, *, now=None, session=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = date.fromisoformat(asof)
    path = Path(cache_dir) / "vn-macro" / "monetary" / (asof + ".json")
    cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
    result = {
        "asof": asof,
        "metrics": {
            k: {
                "value": None,
                "unit": u,
                "asof": asof,
                "source": ROOT,
                "status": "missing",
                "formula": "No eligible dated NHNN briefing fact; no estimate",
            }
            for k, u in UNITS.items()
        },
        "sources": [],
        "releases": [],
        "warnings": [],
        "scope": "Selected NHNN briefings published by DIV. Homepage discovery plus curated seed; coverage/latest-release completeness not guaranteed. No daily policy rate, credit limit, individual-bank growth or historical revision archive.",
    }
    if target > now.date():
        result["warnings"].append("Future monetary query withheld")
        return result
    if target < now.date():
        if cached and datetime.fromisoformat(cached["observed_at"]).astimezone(TZ).date() <= target:
            return cached
        result["warnings"].append(
            "Historical monetary release withheld: no snapshot observed by analysis date"
        )
        return result
    if cached and (now - datetime.fromisoformat(cached["observed_at"])).total_seconds() < 21600:
        return cached
    session = session or requests.Session()
    session.headers.update({"User-Agent": "TradingAgents-Research/0.8", "Accept": "text/html"})

    def fetch(url):
        r = session.get(url, timeout=(8, 20))
        r.raise_for_status()
        if not trusted(r.url) or len(r.content) > MAX_BYTES:
            raise ValueError("Unexpected monetary source redirect or size")
        return r.text

    urls = [SEED]
    try:
        homepage = BeautifulSoup(fetch(ROOT), "html.parser")
        for a in homepage.find_all("a", href=True):
            title = a.get_text(" ", strip=True).lower()
            url = urljoin(ROOT, a["href"])
            if (
                "ngân hàng nhà nước" in title
                and "họp báo" in title
                and trusted(url)
                and url not in urls
            ):
                urls.append(url)
        urls = urls[:4]
    except (requests.RequestException, ValueError) as exc:
        result["warnings"].append("Monetary release discovery: " + type(exc).__name__)
    for url in urls:
        try:
            raw = fetch(url)
            record = parse(raw, url, now)
            write_json(
                path.parent / ("raw_" + record["sha256"] + ".json"),
                {"url": url, "observed_at": now.isoformat(), "html": raw},
            )
            result["releases"].append(record)
            result["sources"].append(
                {k: record[k] for k in ("url", "published", "observed_at", "sha256")}
            )
            result["warnings"].extend(
                "Conflicting monetary fact withheld: " + k for k in record["conflicts"]
            )
        except (requests.RequestException, ValueError) as exc:
            result["warnings"].append("Monetary release: " + type(exc).__name__)
    for alias in UNITS:
        if any(alias in r["conflicts"] for r in result["releases"]):
            continue
        rows = [r["metrics"][alias] for r in result["releases"] if alias in r["metrics"]]
        if not rows:
            continue
        latest = max(r["asof"] for r in rows)
        rows = [r for r in rows if r["asof"] == latest]
        if len({r["value"] for r in rows}) != 1:
            result["warnings"].append("Cross-release conflict withheld: " + alias)
            continue
        metric = rows[0]
        metric["lag_days"] = (now.date() - date.fromisoformat(metric["asof"])).days
        if metric["lag_days"] > 62:
            metric = {
                **metric,
                "value": None,
                "status": "stale",
                "formula": metric["formula"] + "; older than 62 days, withheld",
            }
        result["metrics"][alias] = metric
    result["observed_at"] = now.isoformat()
    if not result["releases"] and cached:
        return {
            **cached,
            "warnings": cached["warnings"]
            + result["warnings"]
            + ["Cached monetary snapshot after source failure"],
        }
    write_json(path, result)
    return result


def tool(bundle, indicator):
    metric = bundle.get("metrics", {}).get(indicator)
    return json.dumps(
        {
            "indicator": indicator,
            "metric": metric,
            "scope": bundle.get("scope"),
            "status": "available" if metric and metric.get("value") is not None else "unavailable",
            "warnings": bundle.get("warnings", []),
        },
        ensure_ascii=False,
    )
