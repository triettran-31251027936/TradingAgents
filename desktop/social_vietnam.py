"""Bounded public F247 tag RSS sample; never a census or measured investor sentiment.

Keep topic links/dates/excerpts, not author profiles or contact data. Current feeds
may contain edited old posts, so historical use requires a locally observed vintage.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from desktop.vietnam import digest, symbol, write_json

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
LOCK = threading.Lock()
CONTEXT_LIMIT = 8
EXCERPT_WORDS = 100
SCOPE = "F247 public tag RSS sample only: first-post excerpts, not thread replies, not independently verified facts or representative investor sentiment. Tags may include other companies. Feed may omit older topics and edited text is not point-in-time. No bullish/bearish counts, sentiment score or unique-author estimate is inferred."


def timestamp(value):
    result = parsedate_to_datetime(value)
    if result.tzinfo is None:
        raise ValueError("RSS date has no timezone")
    return result.astimezone(TZ)


def text_excerpt(html, *, words):
    soup = BeautifulSoup(html or "", "html.parser")
    for el in soup.select("script,style,iframe,small,blockquote,.quote"):
        el.decompose()
    for a in soup.select("a"):
        if "Đọc toàn bộ chủ đề" in a.get_text():
            a.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[contact omitted]", text)
    text = re.sub(r"https?://\S+", "[link omitted]", text)
    text = re.sub(r"(?<!\w)(?:\+?84|0)(?:[ .-]?\d){8,10}(?!\w)", "[contact omitted]", text)
    tokens = text.split()
    return " ".join(tokens[:words]), len(tokens) > words


def relevance(ticker, title, excerpt):
    text = title + " " + excerpt
    # A concrete collision in this feed: FPT Retail explicitly identifies FRT,
    # while its legal name contains FPT. Keep inspectable, exclude as direct FPT.
    if (
        ticker == "FPT"
        and re.search(r"\bFRT\b", text, re.I)
        and re.search(r"FPT\s+RETAIL|Bán lẻ Kỹ thuật số FPT", text, re.I)
    ):
        return "related_company_or_ambiguous"
    return (
        "ticker_in_excerpt_or_title"
        if re.search(r"(?<!\w)" + re.escape(ticker) + r"(?!\w)", text, re.I)
        else "tag_only_unverified"
    )


def parse_feed(raw, ticker, asof, now):
    if len(raw) > 3_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Unsupported RSS size/entities")
    channel = ElementTree.fromstring(raw).find("channel")
    if (
        channel is None
        or channel.findtext("link", "").rstrip("/").lower()
        != ("https://f247.com/tag/" + ticker).lower()
    ):
        raise ValueError("RSS channel differs from requested ticker")
    rows, issues, seen = [], [], set()
    cutoff = min(
        now, datetime.combine(datetime.fromisoformat(asof).date(), datetime.max.time(), TZ)
    )
    for item in channel.findall("item"):
        try:
            link = item.findtext("link", "")
            parsed = urlparse(link)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "f247.com"
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Unexpected topic URL")
            match = re.fullmatch(r"/t/[^/]+/(\d+)", parsed.path.rstrip("/"))
            if not match:
                raise ValueError("Unexpected topic path")
            topic_id = "f247:" + match[1]
            if topic_id in seen:
                raise ValueError("Duplicate RSS topic ID")
            seen.add(topic_id)
            published = timestamp(item.findtext("pubDate", ""))
            if published > cutoff:
                continue
            title, title_cut = text_excerpt(item.findtext("title"), words=24)
            excerpt, excerpt_cut = text_excerpt(item.findtext("description"), words=EXCERPT_WORDS)
            if not title or not excerpt:
                raise ValueError("Missing topic title or text")
            rows.append(
                {
                    "id": topic_id,
                    "title": title,
                    "url": link,
                    "published": published.isoformat(),
                    "excerpt": excerpt,
                    "title_truncated": title_cut,
                    "excerpt_truncated": excerpt_cut,
                    "relevance": relevance(ticker, title, excerpt),
                    "coverage": "RSS first-post excerpt only; replies/images not read; date is RSS pubDate, not proven edit-publication date",
                }
            )
        except (ValueError, TypeError, OverflowError, IndexError) as exc:
            issues.append(type(exc).__name__ + ": " + str(exc))
    rows.sort(key=lambda r: (r["published"], r["id"]), reverse=True)
    return rows, issues, len(channel.findall("item"))


def load(ticker, asof, cache_dir, *, session=None, now=None):
    name = symbol(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = datetime.fromisoformat(asof).date()
    base = {
        "ticker": name + ".VN",
        "asof": asof,
        "source": "F247 public tag RSS",
        "source_url": f"https://f247.com/tag/{name}.rss",
        "scope": SCOPE,
        "records": [],
        "warnings": [],
    }
    if target > now.date():
        return {**base, "status": "withheld", "reason": "Future analysis date"}
    folder = Path(cache_dir) / "vn-social" / name
    folder.mkdir(parents=True, exist_ok=True)
    with LOCK:
        path = folder / "latest.json"
        cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
        error = None
        if target < now.date():
            candidates = []
            for p in folder.glob("*.snapshot.json"):
                if p.is_symlink():
                    continue
                row = json.loads(p.read_text())
                if datetime.fromisoformat(row["first_observed_at"]).astimezone(TZ).date() <= target:
                    candidates.append(row)
            if not candidates:
                return {
                    **base,
                    "status": "withheld",
                    "reason": "No observed RSS vintage by analysis date; current text may contain later edits",
                }
            cached = max(candidates, key=lambda r: r["first_observed_at"])
        elif (
            not cached
            or now.timestamp() - datetime.fromisoformat(cached["retrieved_at"]).timestamp() >= 1800
        ):
            session = session or requests.Session()
            session.headers.update(
                {"User-Agent": "TradingAgents-Research/0.8", "Accept": "application/rss+xml"}
            )
            try:
                response = session.get(base["source_url"], timeout=(8, 25))
                response.raise_for_status()
                rows, issues, total = parse_feed(response.content, name, asof, now)
                # Raw source hash, but persist only projected topic evidence: no creators,
                # profile URLs, avatars, email/contact fields or tracking markup.
                version = digest({"records": rows, "issues": issues, "source_item_count": total})
                snapshot = folder / (version + ".snapshot.json")
                old = (
                    json.loads(snapshot.read_text())
                    if snapshot.is_file() and not snapshot.is_symlink()
                    else None
                )
                cached = {
                    "records": rows,
                    "issues": issues,
                    "source_item_count": total,
                    "source_sha256": hashlib.sha256(response.content).hexdigest(),
                    "version": version,
                    "retrieved_at": now.isoformat(),
                    "first_observed_at": old["first_observed_at"] if old else now.isoformat(),
                }
                write_json(snapshot, cached)
                write_json(path, cached)
            except (requests.RequestException, ValueError, ElementTree.ParseError) as exc:
                if not cached:
                    return {
                        **base,
                        "status": "unavailable",
                        "reason": type(exc).__name__,
                        "warnings": [
                            "Community source unavailable; no observed sentiment, not neutral/zero activity"
                        ],
                    }
                error = type(exc).__name__
        end = min(now, datetime.combine(target, datetime.max.time(), TZ))
        start = target - timedelta(days=7)
        rows = [
            r
            for r in cached["records"]
            if start <= datetime.fromisoformat(r["published"]).astimezone(TZ).date()
            and datetime.fromisoformat(r["published"]) <= end
        ]
        rows = [{**r, "relevance": relevance(name, r["title"], r["excerpt"])} for r in rows]
        context = [r for r in rows if r["relevance"] == "ticker_in_excerpt_or_title"][
            :CONTEXT_LIMIT
        ]
        return {
            **base,
            "status": "partial"
            if cached["issues"]
            else "cached_source_error"
            if error
            else "available",
            "records": rows,
            "source_item_count": cached["source_item_count"],
            "source_sha256": cached["source_sha256"],
            "retrieved_at": cached["retrieved_at"],
            "first_observed_at": cached["first_observed_at"],
            "window_start": start.isoformat(),
            "window_end": asof,
            "context_records": context,
            "context_omitted_count": len(rows) - len(context),
            "context_policy": "At most eight newest records explicitly mentioning ticker in supplied title/excerpt; tag-only records retained for inspection but excluded from model context",
            "warnings": [
                *cached["issues"],
                *(["Cached source retained after " + error] if error else []),
                "Bounded RSS sample; a tag is not proof of company relevance; excerpts are opinions, not verified facts. Zero retained rows means no observed sample, not no discussion.",
            ],
        }


def summary(bundle):
    return {k: v for k, v in bundle.items() if k != "records"} | {
        "retained_record_count": len(bundle.get("records", []))
    }
