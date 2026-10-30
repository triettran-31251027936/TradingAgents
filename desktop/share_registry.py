"""Current VSDC registered-share evidence, distinct from outstanding shares."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from desktop.vietnam import TZ, symbol, write_json

ROOT = "https://vsdc.vn"
SEARCH = ROOT + "/search-suggest"
SCOPE = "VSDC registered securities, NOT automatically shares outstanding or free float. Current observation, not a historical vintage archive."


def integer(text, suffix=""):
    match = re.fullmatch(r"(-?(?:\d{1,3}(?:\.\d{3})+|\d+))\s*" + re.escape(suffix), text.strip())
    if not match:
        raise ValueError("Invalid registry integer")
    return int(match[1].replace(".", ""))


def parse(raw, ticker, asof):
    ticker = symbol(ticker)
    if len(raw.encode()) > 2_000_000:
        raise ValueError("Oversized registry document")
    soup = BeautifulSoup(raw, "html.parser")
    fields = {}
    for row in soup.select("div.row"):
        cells = row.find_all("div", recursive=False)
        if len(cells) != 2 or not all("item-info" in c.get("class", []) for c in cells):
            continue
        key, value = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
        key = re.sub(r"\s+:", ":", key)
        if key in fields:
            raise ValueError("Duplicate registry field")
        fields[key] = value
    if fields.get("Mã chứng khoán:") != ticker or fields.get("Loại chứng khoán:") != "Cổ phiếu":
        raise ValueError("Mismatched ticker or non-stock registry instrument")
    isin = fields.get("Mã ISIN:", "")
    if not re.fullmatch(r"VN[A-Z0-9]{9}\d", isin):
        raise ValueError("Invalid registry ISIN")
    count = integer(fields.get("Tổng số chứng khoán đăng ký:", ""), "Cổ phiếu")
    par = integer(fields.get("Mệnh giá:", ""), "đồng")
    if count <= 0 or par <= 0:
        raise ValueError("Non-positive registry shares/par")
    tables = [
        t
        for t in soup.find_all("table")
        if [c.get_text(" ", strip=True) for c in t.select("th")]
        == ["STT", "Lần đăng ký", "Lý do", "Số lượng", "Số GCNĐKCK", "Ngày"]
    ]
    if len(tables) != 1:
        raise ValueError("Missing or ambiguous registry history")
    history, total = [], None
    for row in tables[0].find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td", recursive=False)]
        if not cells:
            continue
        if cells[0] == "Tổng cộng:" and len(cells) >= 2 and not any(cells[2:]):
            if total is not None:
                raise ValueError("Duplicate registry total")
            total = integer(cells[1])
            continue
        if len(cells) != 6 or integer(cells[0]) != len(history) + 1:
            raise ValueError("Incomplete registry history")
        day = datetime.strptime(cells[5], "%d/%m/%Y").date().isoformat()
        if day > asof:
            raise ValueError("Future registration event")
        history.append({"date": day, "change": integer(cells[3]), "reason": cells[2]})
    if not history or total != count or sum(r["change"] for r in history) != count:
        raise ValueError("Registry history does not reconcile to current registered shares")
    return {
        "ticker": ticker + ".VN",
        "isin": isin,
        "exchange": fields.get("Nơi giao dịch (*):"),
        "registered_shares": count,
        "par_value_vnd": par,
        "registration_history": history,
        "latest_registration_date": max(r["date"] for r in history),
        "history_reconciled": True,
    }


def load(ticker, asof, cache_dir, *, now=None, session=None):
    ticker = symbol(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = datetime.strptime(asof, "%Y-%m-%d").date()
    path = Path(cache_dir) / "vn-registry" / ticker / (asof + ".json")
    empty = {"ticker": ticker + ".VN", "status": "unavailable", "source_url": ROOT, "scope": SCOPE}
    cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
    if target > now.date():
        return {**empty, "status": "withheld", "reason": "Future registry date"}
    if target < now.date():
        if cached and datetime.fromisoformat(cached["observed_at"]).astimezone(TZ).date() <= target:
            return cached
        return {
            **empty,
            "status": "withheld",
            "reason": "No registry snapshot observed by historical analysis date",
        }
    if cached and (now - datetime.fromisoformat(cached["observed_at"])).total_seconds() < 21600:
        return cached
    session = session or requests.Session()
    session.headers.update({"User-Agent": "TradingAgents-Research/0.8"})

    def response_text(response):
        response.raise_for_status()
        host = urlparse(response.url).hostname
        if host != "vsdc.vn" or len(response.content) > 2_000_000:
            raise ValueError("Unexpected registry host or document size")
        return response.text

    try:
        home = BeautifulSoup(
            response_text(session.get(ROOT + "/vi/", timeout=(8, 20))), "html.parser"
        )
        csrf = home.select_one('meta[name="__VPToken"]')
        response = session.post(
            SEARCH,
            json={"text": ticker, "type": "5"},
            headers={"__VPToken": csrf["content"]} if csrf else {},
            timeout=(8, 20),
        )
        data = json.loads(response_text(response))
        if data.get("success") != 0 or not isinstance(data.get("data"), list):
            raise ValueError("Invalid registry search response")
        matches = [
            r
            for r in data["data"]
            if re.match(re.escape(ticker) + r":\s*Cổ phiếu\b", str(r.get("content", "")))
            and "hủy đăng ký" not in str(r.get("content", "")).casefold()
        ]
        if len(matches) != 1:
            raise ValueError("No unique exact stock registry match")
        href = matches[0].get("href", "")
        if not isinstance(href, str) or not re.fullmatch(r"/vi/s-detail/[1-9]\d{0,8}", href):
            raise ValueError("Invalid stock registry detail link")
        url = ROOT + href
        raw = response_text(session.get(url, timeout=(8, 20)))
        # Session/CSRF material is not part of the archived financial evidence.
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup.select('meta[name="__VPToken"],script,style'):
            tag.decompose()
        raw = str(soup)
        record = {
            **parse(raw, ticker, asof),
            "status": "available",
            "source_url": url,
            "observed_at": now.isoformat(),
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "scope": SCOPE,
        }
        write_json(
            path.parent / ("raw_" + record["sha256"] + ".json"),
            {"url": url, "observed_at": now.isoformat(), "html": raw},
        )
        write_json(path, record)
        return record
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        if cached:
            return {**cached, "status": "cached_source_error", "reason": type(exc).__name__}
        return {**empty, "reason": type(exc).__name__}
