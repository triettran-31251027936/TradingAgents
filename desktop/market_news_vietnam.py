"""Attributed VnEconomy RSS context alongside the unchanged upstream news tools.

One publisher, three bounded feeds. Headlines and RSS excerpts are secondary
reporting, not canonical macro facts or an archive of all market news.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from desktop import rss_archive
from desktop.company_profile import plain
from desktop.vietnam import digest

TZ = rss_archive.TZ
stamp = rss_archive.stamp
LOCK = threading.Lock()
FEEDS = {
    "finance": "https://vneconomy.vn/tai-chinh.rss",
    "stocks": "https://vneconomy.vn/chung-khoan.rss",
    "world": "https://vneconomy.vn/kinh-te-the-gioi.rss",
}
SCOPE = (
    "VnEconomy / Tạp chí Kinh tế Việt Nam, one publisher across three RSS feeds. "
    "Headlines and at most 60 words of RSS excerpt per item; full articles not read. "
    "Secondary reporting, not verified macro facts, representative sentiment or exhaustive news. "
    "RSS publication date does not prove when later edits became available."
)


def parse(raw, feed):
    if len(raw) > 3_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Unsupported RSS size/entities")
    channel = ElementTree.fromstring(raw).find("channel")
    if channel is None or channel.findtext("link", "").rstrip("/") != FEEDS[feed].removesuffix(
        ".rss"
    ):
        raise ValueError("RSS channel differs from selected source")
    records, issues = [], []
    for item in channel.findall("item"):
        try:
            link = item.findtext("link", "")
            url = urlparse(link)
            if (
                url.scheme != "https"
                or url.netloc != "vneconomy.vn"
                or url.query
                or url.fragment
                or not url.path.endswith(".htm")
            ):
                raise ValueError("Unexpected article link")
            published = parsedate_to_datetime(item.findtext("pubDate", ""))
            if published.tzinfo is None:
                raise ValueError("RSS publication date has no timezone")
            title = plain(item.findtext("title", ""))
            if not title:
                raise ValueError("Missing article title")
            words = plain(item.findtext("description", "")).split()
            records.append(
                {
                    "id": "market:" + hashlib.sha256(link.encode()).hexdigest()[:24],
                    "title": title,
                    "url": link,
                    "published": published.astimezone(TZ).isoformat(),
                    "excerpt": " ".join(words[:60]),
                    "excerpt_truncated": len(words) > 60,
                    "publisher": "VnEconomy / Tạp chí Kinh tế Việt Nam",
                    "feeds": [feed],
                }
            )
        except (ValueError, TypeError, OverflowError) as exc:
            issues.append(type(exc).__name__ + ": " + str(exc))
    return records, issues, len(channel.findall("item"))


def load(asof, cache_dir, *, look_back_days=7, limit=10, session=None, now=None):
    if type(limit) is not int or limit < 1 or type(look_back_days) is not int or look_back_days < 0:
        raise ValueError("Market news requires positive article limit and nonnegative lookback")
    fixed_now = now is not None
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = datetime.fromisoformat(asof).date()
    end = min(now, datetime.combine(target, time.max, TZ))
    start = datetime.combine(target - timedelta(days=look_back_days), time.min, TZ)
    base = {
        "asof": asof,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "article_limit": limit,
        "look_back_days": look_back_days,
        "scope": SCOPE,
        "records": [],
        "sources": [],
        "warnings": [],
    }
    if target > now.date():
        return {**base, "status": "withheld", "reason": "Future analysis date"}
    session = session or requests.Session()
    session.headers.update(
        {"User-Agent": "TradingAgents-Research/0.8", "Accept": "application/rss+xml"}
    )
    by_url = {}
    usable = 0
    with LOCK:
        for feed, url in FEEDS.items():
            folder = Path(cache_dir) / "vn-market-news" / feed
            folder.mkdir(parents=True, exist_ok=True)
            cached = rss_archive.read(folder / "latest.json")
            error = None
            if target < now.date():
                cached = rss_archive.replay(folder, end, cached)
            elif not cached or now.timestamp() - stamp(cached["retrieved_at"]).timestamp() >= 900:
                try:
                    response = session.get(url, timeout=(8, 25))
                    response.raise_for_status()
                    retrieved = now if fixed_now else datetime.now(TZ)
                    if retrieved.date() != target:
                        raise ValueError("RSS retrieval crossed the analysis date cutoff")
                    records, issues, total = parse(response.content, feed)
                    version = digest(
                        {"records": records, "issues": issues, "source_item_count": total}
                    )
                    path = folder / (version + ".snapshot.json")
                    old = rss_archive.read(path)
                    observed = {
                        "records": records,
                        "issues": issues,
                        "source_item_count": total,
                        "source_sha256": hashlib.sha256(response.content).hexdigest(),
                        "first_observed_at": old["first_observed_at"] if old else retrieved.isoformat(),
                        "retrieved_at": retrieved.isoformat(),
                    }
                    rss_archive.save(folder, path, old, cached, observed)
                    cached = observed
                except (requests.RequestException, ValueError, ElementTree.ParseError) as exc:
                    error = type(exc).__name__
            source = {
                "name": "VnEconomy RSS · " + feed,
                "url": url,
                "status": "withheld"
                if target < now.date() and not cached
                else "unavailable"
                if not cached
                else "cached_source_error"
                if error
                else "partial"
                if cached["issues"]
                else "available",
            }
            if not cached:
                source["reason"] = (
                    "No observed feed vintage by analysis date"
                    if target < now.date()
                    else error or "No source response"
                )
                base["warnings"].append(feed + ": " + source["reason"])
            else:
                usable += 1
                source.update(
                    retrieved_at=cached["retrieved_at"],
                    first_observed_at=cached["first_observed_at"],
                    sha256=cached["source_sha256"],
                    source_item_count=cached["source_item_count"],
                )
                base["warnings"].extend(feed + ": " + issue for issue in cached["issues"])
                if error:
                    base["warnings"].append(feed + ": cached source retained after " + error)
                for row in cached["records"]:
                    if start <= stamp(row["published"]) <= end:
                        if row["url"] in by_url:
                            prior = by_url[row["url"]]
                            # Conflicting copies are not independent corroboration.
                            if any(prior[k] != row[k] for k in ("title", "published", "excerpt")):
                                prior["conflicting_feed_copies"] = True
                            prior["feeds"] = sorted(set(prior["feeds"] + row["feeds"]))
                        else:
                            by_url[row["url"]] = {
                                **row,
                                "first_observed_at": cached["first_observed_at"],
                            }
            base["sources"].append(source)
    eligible = sorted(by_url.values(), key=lambda r: (r["published"], r["id"]), reverse=True)
    conflicts = [r for r in eligible if r.get("conflicting_feed_copies")]
    if conflicts:
        base["warnings"].append(
            f"{len(conflicts)} article copies conflict across feeds; withheld from context"
        )
    eligible = [r for r in eligible if not r.get("conflicting_feed_copies")]
    selected = eligible[:limit]
    bad = any(source["status"] != "available" for source in base["sources"])
    return {
        **base,
        "status": "withheld"
        if not usable and target < now.date()
        else "unavailable"
        if not usable
        else "partial"
        if bad or conflicts
        else "available"
        if selected
        else "empty_sample",
        "records": selected,
        "eligible_unique_count": len(eligible),
        "omitted_count": max(0, len(eligible) - len(selected)),
        "selection": "Newest unique URLs across three feeds, at most configured article limit; not query-ranked or representative. Three feeds are one publisher, not independent corroboration.",
    }


def summary(bundle):
    return {k: v for k, v in bundle.items() if k != "sources"}

def tool(snapshot, asof, look_back_days, limit):
    if type(limit) is not int or limit < 1 or type(look_back_days) is not int or look_back_days < 0:
        raise ValueError("Global news requires positive article limit and nonnegative lookback")
    target = datetime.fromisoformat(asof).date()
    start = target - timedelta(days=look_back_days)
    result = {
        "status": "withheld",
        "scope": SCOPE,
        "requested_dates": [start.isoformat(), asof],
        "article_limit": limit,
        "records": [],
    }
    if snapshot.get("asof") != asof or not snapshot.get("window_end"):
        return {**result, "reason": "No saved RSS observation for this analysis date"}
    cutoff = stamp(snapshot["window_end"])
    selected = [
        row for row in snapshot.get("records", [])
        if (published := stamp(row["published"])).date() >= start and published <= cutoff
    ]
    status = snapshot["status"]
    if status == "available" and not selected:
        status = "empty_sample"
    return {
        **result,
        "status": status,
        "saved_window": [snapshot["window_start"], snapshot["window_end"]],
        "saved_article_limit": snapshot["article_limit"],
        "sources": snapshot.get("sources", []),
        "records": selected[:limit],
        "coverage": "Only the overlap of the requested dates and saved bounded sample is returned.",
    }
