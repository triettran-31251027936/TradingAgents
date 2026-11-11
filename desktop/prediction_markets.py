"""Polymarket observations for Vietnam analyses and dated replay."""

import json
import math
import re
from datetime import date, datetime
from pathlib import Path

import requests

from desktop.vietnam import TZ, digest, write_json
from tradingagents.dataflows.vendors.polymarket import DEFAULT_LIMIT, GAMMA_BASE, _parse_json_list

SEARCH = GAMMA_BASE + "/public-search"
EVENTS = GAMMA_BASE + "/events"
MAX_MARKETS = 20
FED = re.compile(r"\b(?:fed|fomc|federal reserve)\b", re.I)
CUT = re.compile(r"\b(?:cut|cuts|reduce|reduction|decrease)\b", re.I)
HIKE = re.compile(r"\b(?:hike|hikes|raise|increase)\b", re.I)


def _get(session, url, params):
    reply = session.get(url, params=params, timeout=(5, 9))
    reply.raise_for_status()
    return reply.json()


def _matches_fed(topic, title):
    if CUT.search(topic) and not CUT.search(title):
        return False
    if HIKE.search(topic) and not HIKE.search(title):
        return False
    dates = set(re.findall(r"\b(?:20\d\d|january|february|march|april|may|june|july|august|"
                           r"september|october|november|december)\b", topic, re.I))
    return all(re.search(r"\b" + word + r"\b", title, re.I) for word in dates)


def _markets(events, observed_at, topic, fallback):
    found = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("markets"), list):
            continue
        event_title = str(event.get("title") or "")
        slug = event.get("slug")
        url = (
            f"https://polymarket.com/event/{slug}"
            if isinstance(slug, str) and re.fullmatch(r"[a-z0-9-]+", slug)
            else None
        )
        for market in event["markets"]:
            try:
                market_id, question = market["id"], market["question"]
                if not isinstance(market_id, str) or not isinstance(question, str) or not question.strip():
                    continue
                if market.get("closed") is not False:
                    continue
                end = datetime.fromisoformat(market["endDate"].replace("Z", "+00:00"))
                if end <= observed_at:
                    continue
                if fallback and not _matches_fed(topic, event_title + " " + question):
                    continue
                labels = _parse_json_list(market["outcomes"])
                prices = _parse_json_list(market["outcomePrices"])
                if not isinstance(labels, list) or not labels or not isinstance(prices, list):
                    continue
                if len(labels) != len(prices) or any(not isinstance(label, str) or not label for label in labels):
                    continue
                odds = [float(price) for price in prices]
                if any(not 0 <= p <= 1 for p in odds) or not math.isclose(sum(odds), 1, abs_tol=0.025):
                    continue
                volume = float(market.get("volumeNum") or 0)
                if not math.isfinite(volume) or volume < 0:
                    continue
                weekly = market.get("oneWeekPriceChange")
                weekly_pp = weekly * 100 if type(weekly) in (int, float) and -1 <= weekly <= 1 else None
                found[market_id] = {
                    "id": market_id, "question": question, "url": url,
                    "resolves_at": end.isoformat(), "volume_usd": volume,
                    "first_outcome_weekly_change_pp": weekly_pp,
                    "outcomes": [{"label": label, "price": price}
                                 for label, price in zip(labels, odds, strict=True)],
                }
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
    return sorted(found.values(), key=lambda row: (-row["volume_usd"], row["id"]))[:MAX_MARKETS]


def _read(folder, topic, asof, now):
    rows = []
    for path in folder.glob("*.json"):
        try:
            if path.is_symlink() or not 0 < path.stat().st_size <= 256 * 1024:
                continue
            row = json.loads(path.read_text())
            observed = datetime.fromisoformat(row["observed_at"])
            if (
                observed.tzinfo is not None and observed <= now
                and observed.astimezone(TZ).date().isoformat() == asof
                and row["topic"] == topic and row["source"] in (SEARCH, EVENTS)
                and path.stem == digest(row)
            ):
                rows.append((observed, path.name, row))
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return max(rows)[2] if rows else None


def tool(topic, limit, asof, cache_dir, *, now=None, session=None):
    topic = " ".join(topic.split())
    target = date.fromisoformat(asof)
    if not topic or len(topic) > 180 or target.isoformat() != asof:
        raise ValueError("Prediction market requires a bounded topic and valid date")
    limit = DEFAULT_LIMIT if limit is None else limit
    if type(limit) is not int or limit < 1:
        raise ValueError("Prediction-market limit must be positive")
    fixed_now = now is not None
    now = (now or datetime.now(TZ)).astimezone(TZ)
    key = topic.casefold()
    folder = Path(cache_dir) / "prediction-markets" / asof / digest(key)
    if target != now.date():
        saved = _read(folder, key, asof, now) if target < now.date() else None
        if saved:
            return json.dumps({**saved, "markets": saved["markets"][:limit], "replayed": True}, ensure_ascii=False)
        return json.dumps({
            "status": "withheld", "asof": asof, "topic": topic, "markets": [],
            "reason": "No matching observation recorded on that date. Later odds are excluded.",
        }, ensure_ascii=False)
    session = session or requests.Session()
    source = SEARCH
    try:
        try:
            payload = _get(session, SEARCH, {"q": topic, "limit_per_type": 20, "events_status": "active",
                                            "search_profiles": "false", "search_tags": "false"})
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                raise ValueError("Unexpected Polymarket search schema")
            events = payload["events"]
        except (requests.Timeout, requests.ConnectionError) as exc:
            if not FED.search(topic) or isinstance(exc, requests.exceptions.SSLError):
                raise
            source = EVENTS
            payload = _get(session, EVENTS, {"tag_slug": "fed", "closed": "false", "limit": 20,
                                            "order": "volume", "ascending": "false"})
            if not isinstance(payload, list):
                raise ValueError("Unexpected Polymarket event schema")
            events = payload
        observed = now if fixed_now else datetime.now(TZ)
        if observed.date() != target:
            raise ValueError("Polymarket retrieval crossed the analysis date")
        markets = _markets(events, observed, topic, source == EVENTS)
    except (requests.RequestException, ValueError) as exc:
        return json.dumps({"status": "unavailable", "asof": asof, "topic": topic, "markets": [],
                           "source": source, "error": type(exc).__name__}, ensure_ascii=False)
    row = {
        "status": "available" if markets else "empty_sample",
        "topic": key, "asof": asof, "observed_at": observed.isoformat(), "source": source,
        "scope": (
            "Polymarket quoted outcome prices are market-implied odds. "
            "Results are a bounded sample; absence is not evidence of no markets. "
            "Volume is cumulative, not order-book depth."
        ),
        "source_sha256": digest(payload), "markets": markets,
    }
    write_json(folder / (digest(row) + ".json"), row)
    return json.dumps({**row, "markets": markets[:limit], "replayed": False}, ensure_ascii=False)
