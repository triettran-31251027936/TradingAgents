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
# Dated reports of the same briefings. A historical fact must match both
# publications, including the comparison base and observation period.
HISTORICAL_BRIEFINGS = (
    ("2026-10-08", SEED, "2026-10-07", "https://vov.gov.vn/tang-truong-tin-dung-1669-lai-suat-van-chiu-suc-ep-dtnew-1168485"),
    (
        "2026-07-02",
        ROOT + "ngan-hang-nha-nuoc-hop-bao-ve-ket-qua-dieu-hanh-chinh-sach-tien-te-va-hoat-dong-ngan-hang-6-thang-dau-nam-2026",
        "2026-07-02",
        "https://vov.gov.vn/du-no-tin-dung-toan-he-thong-dat-tren-1997-trieu-ty-dong-dtnew-1152291",
    ),
)
UNITS = {
    "vn_credit_stock": "VND",
    "vn_credit_ytd": "fraction",
    "vn_credit_yoy": "fraction",
    "vn_new_deposit_rate": "fraction/year",
}
MAX_BYTES = 2_000_000


def trusted(url, *, corroboration=False):
    p = urlparse(url)
    host = "vov.gov.vn" if corroboration else "div.gov.vn"
    return p.scheme == "https" and p.netloc == host and not p.query and not p.fragment


def parse(raw, url, observed, *, corroboration=False):
    if observed.tzinfo is None:
        raise ValueError("Monetary observation requires timezone")
    if not trusted(url, corroboration=corroboration) or len(raw.encode()) > MAX_BYTES:
        raise ValueError("Untrusted or oversized monetary release")
    soup = BeautifulSoup(raw, "html.parser")
    body, stamp, title = (
        soup.select_one(".entry-content"),
        soup.select_one(".date_post_core"),
        soup.find("h1"),
    )
    if corroboration:
        parts = soup.select(".muc03, .chiTietTinBai")
        stamp = soup.select_one(".text-2 span")
        title = soup.find("title")
    else:
        parts = [body] if body is not None else []
    if not parts or stamp is None or title is None:
        raise ValueError("Monetary release schema changed")
    published = re.search(r"\b(\d{2})/(\d{2})/(20\d{2})\b", stamp.get_text())
    title_text = title.get_text(" ", strip=True)
    if not published or (
        not corroboration and (
            "ngân hàng nhà nước" not in title_text.lower()
            or "họp báo" not in title_text.lower()
        )
    ):
        raise ValueError("Not a dated NHNN briefing")
    published = date(int(published[3]), int(published[2]), int(published[1]))
    if published > observed.date():
        raise ValueError("Future monetary release")
    text = " ".join(" ".join(part.get_text(" ", strip=True).split()) for part in parts)
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
    pattern = rf"đến ngày (\d{{1,2}})/(\d{{1,2}})/(20\d{{2}}), dư nợ tín dụng toàn hệ thống đạt (trên |khoảng )?({number}) triệu tỷ đồng, tăng ({number})% so với cuối năm (20\d{{2}})(?: và |[,;]\s*)tăng ({number})% so với cùng kỳ năm (20\d{{2}})"
    for m in re.finditer(pattern, text, re.I):
        if projected(m):
            continue
        period = date(int(m[3]), int(m[2]), int(m[1]))
        if int(m[7]) != period.year - 1 or int(m[9]) != period.year - 1:
            continue
        if m[4] is None:  # "trên" is a lower bound, not the outstanding balance.
            add("vn_credit_stock", float(m[5].replace(",", ".")) * 1e15, period)
        add("vn_credit_ytd", float(m[6].replace(",", ".")) / 100, period)
        add("vn_credit_yoy", float(m[8].replace(",", ".")) / 100, period)
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
        "title": title_text,
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
    historical = target < now.date()
    if historical and cached and (
        datetime.fromisoformat(cached["observed_at"]).astimezone(TZ).date() <= target
        or cached.get("source_basis") == "cross_checked_dated_briefings"
    ):
        return cached
    if not historical and cached and (now - datetime.fromisoformat(cached["observed_at"])).total_seconds() < 21600:
        return cached
    session = session or requests.Session()
    session.headers.update({"User-Agent": "TradingAgents-Research/0.8", "Accept": "text/html"})

    def fetch(url, *, corroboration=False):
        r = session.get(url, timeout=(8, 20))
        r.raise_for_status()
        if not trusted(r.url, corroboration=corroboration) or len(r.content) > MAX_BYTES:
            raise ValueError("Unexpected monetary source redirect or size")
        return r.text

    def archive(raw, record):
        write_json(
            path.parent / ("raw_" + record["sha256"] + ".json"),
            {"url": record["url"], "observed_at": now.isoformat(), "html": raw},
        )
        result["sources"].append(
            {k: record[k] for k in ("url", "published", "observed_at", "sha256")}
        )

    if historical:
        result["scope"] += " Historical facts require matching DIV and VOV dated reports, retrieved later."
        for published, url, witness_date, witness_url in HISTORICAL_BRIEFINGS:
            if not 0 <= (target - date.fromisoformat(published)).days <= 62:
                continue
            try:
                raw = fetch(url)
                record = parse(raw, url, now)
                witness_raw = fetch(witness_url, corroboration=True)
                witness = parse(witness_raw, witness_url, now, corroboration=True)
                if record["published"] != published or witness["published"] != witness_date:
                    raise ValueError("Dated reports do not match the curated publication dates")
                for alias, metric in list(record["metrics"].items()):
                    other = witness["metrics"].get(alias)
                    if other is None or (other["asof"], other["value"]) != (metric["asof"], metric["value"]):
                        del record["metrics"][alias]
                        result["warnings"].append("Historical monetary fact not corroborated: " + alias)
                    else:
                        metric["corroborating_source"] = witness_url
                        metric["corroborating_sha256"] = witness["sha256"]
                        metric["formula"] += "; matched to the dated VOV report, retrieved after analysis date"
                archive(raw, record)
                archive(witness_raw, witness)
                result["releases"].append(record)
            except (requests.RequestException, ValueError) as exc:
                result["warnings"].append("Historical monetary release: " + type(exc).__name__)
        if not result["releases"]:
            result["warnings"].append("No eligible corroborated historical NHNN release")
    else:
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
                archive(raw, record)
                result["releases"].append(record)
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
        metric["lag_days"] = (target - date.fromisoformat(metric["asof"])).days
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
    if historical and any(m["value"] is not None for m in result["metrics"].values()):
        result["source_basis"] = "cross_checked_dated_briefings"
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
