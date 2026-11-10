"""Current VSDC registered-share evidence, distinct from outstanding shares."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from desktop.vietnam import TZ, symbol, write_json

ROOT = "https://vsdc.vn"
SEARCH = ROOT + "/search-suggest"
SCOPE = "VSDC registered securities, NOT automatically shares outstanding or free float. Current observation, not a historical vintage archive."


# Manually verified issuer disclosures keyed by VSDC registration event. The
# published PDF must still match before a receipt can enter the data snapshot.
ISSUER_CAPITAL_NOTICES = {
    ("PVS", "2026-10-14"): {
        "issuer": "PTSC",
        "published": "2026-10-19",
        "effective_date": "2026-10-16",
        "before_shares": 511420099,
        "after_shares": 613699317,
        "par_value_vnd": 10000,
        "url": "https://www.ptsc.com.vn/Data/Sites/1/media/codong/2026/1706%20TB-PTSC_.pdf",
        "sha256": "fe06be0deceb266bbf76af1cba5d136e48900bc7c1fd02cc38900cc6ebc0adee",
    },
}


def _issuer_capital_change(ticker, event, asof, session):
    notice = ISSUER_CAPITAL_NOTICES.get((ticker, event["date"]))
    if not notice or notice["published"] > asof:
        return None
    try:
        response = session.get(notice["url"], timeout=(8, 12))
        response.raise_for_status()
        if (
            urlparse(response.url).hostname != urlparse(notice["url"]).hostname
            or hashlib.sha256(response.content).hexdigest() != notice["sha256"]
        ):
            return None
    except requests.RequestException:
        return None
    return dict(notice)


def integer(text, suffix=""):
    match = re.fullmatch(r"(-?(?:\d{1,3}(?:\.\d{3})+|\d+))\s*" + re.escape(suffix), text.strip())
    if not match:
        raise ValueError("Invalid registry integer")
    return int(match[1].replace(".", ""))


def _fields(soup):
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
    return fields


def parse(raw, ticker, asof):
    ticker = symbol(ticker)
    if len(raw.encode()) > 2_000_000:
        raise ValueError("Oversized registry document")
    soup = BeautifulSoup(raw, "html.parser")
    fields = _fields(soup)
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
        quantity = integer(cells[3])
        history.append(
            {
                "date": day,
                "change": quantity,
                "reported_quantity": quantity,
                "registration": cells[1],
                "certificate": cells[4],
                "reason": cells[2],
            }
        )
    if not history or total != count:
        raise ValueError("Registry history does not reconcile to current registered shares")
    quantity_sum = sum(r["reported_quantity"] for r in history)
    method = "additive_registration_history"
    if quantity_sum != count:
        # VSDC can first register only an IPO auction tranche, then register
        # the whole company after equitization. That second row replaces the
        # tranche balance; it is not another incremental issuance. Require
        # both explicit source descriptions and the complete later arithmetic,
        # never choose an arbitrary subset that happens to match the total.
        replacement = (
            len(history) >= 2
            and "cổ phiếu trúng đấu giá" in history[0]["reason"].casefold()
            and history[1]["reason"].casefold() == "đăng ký sau khi cổ phần hóa"
            and 0 < history[0]["reported_quantity"] < history[1]["reported_quantity"]
            and history[0]["date"] < history[1]["date"]
            and all(a["date"] <= b["date"] for a, b in zip(history[:-1], history[1:], strict=True))
            and sum(r["reported_quantity"] for r in history[1:]) == count
        )
        if not replacement:
            raise ValueError("Registry history does not reconcile to current registered shares")
        method = "ipo_tranche_replaced_by_full_post_equitization_registration"
        for row, kind in zip(
            history[:2], ("ipo_auction_tranche", "whole_company_registration"), strict=True
        ):
            row["quantity_basis"] = kind
            row["change"] = None  # Neither source quantity is a new incremental issuance.
    return {
        "ticker": ticker + ".VN",
        "isin": isin,
        "exchange": fields.get("Nơi giao dịch (*):"),
        "registered_shares": count,
        "par_value_vnd": par,
        "registration_history": history,
        "latest_registration_date": max(r["date"] for r in history),
        "history_reconciled": True,
        "history_reconciliation": {
            "method": method,
            "reported_quantity_sum": quantity_sum,
            "current_total": count,
            "scope": "Reconciles registered securities, not outstanding shares. An explicitly labelled post-equitization full registration replaces its preceding IPO auction tranche; original quantities remain retained.",
        },
    }


def response_text(response):
    response.raise_for_status()
    host = urlparse(response.url).hostname
    if host != "vsdc.vn" or len(response.content) > 2_000_000:
        raise ValueError("Unexpected registry host or document size")
    return response.text


def fetch_registry(ticker, session):
    session.headers.update({"User-Agent": "TradingAgents-Research/0.8"})
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
    return url, raw


def sanitized_page(raw):
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup.select('meta[name="__VPToken"],script,style'):
        tag.decompose()
    return soup


def _dated_registration(soup, registry, ticker, asof, session):
    """Confirm the latest registry balance was publicly disclosed by the cutoff."""
    expected_day = registry["latest_registration_date"]
    if expected_day > asof:
        raise ValueError("Current registration changed after historical cutoff")
    expected = re.compile(
        re.escape(ticker) + r": Điều chỉnh thông tin số lượng (?:cổ phiếu|chứng khoán) đăng ký",
        re.I,
    )
    links = [
        row for row in soup.select("#divlistArticlesTCPH_TCDK ul.list-news > li")
        if (link := row.select_one("h3 a[href]")) and expected.fullmatch(link.get_text(" ", strip=True))
        and expected_day <= _notice_time(row.select_one(".time-news")).date().isoformat() <= asof
    ]
    if len(links) != 1:
        raise ValueError("No unique dated VSDC balance notice matching latest registration")
    row = links[0]
    href = row.select_one("h3 a[href]")["href"]
    if not re.fullmatch(r"/(?:vi/)?ad/[1-9]\d{0,8}", href):
        raise ValueError("Invalid registration notice link")
    url = ROOT + href
    notice = sanitized_page(response_text(session.get(url, timeout=(8, 20))))
    title = notice.select_one("h3.title-category")
    stamp = _notice_time(notice.select_one(".time-newstcph"))
    if not title or not expected.fullmatch(title.get_text(" ", strip=True)) or stamp != _notice_time(
        row.select_one(".time-news")
    ):
        raise ValueError("VSDC balance notice identity or date differs from issuer listing")
    fields = _fields(notice)
    if (
        fields.get("Mã chứng khoán:") != ticker
        or fields.get("Mã ISIN:") != registry["isin"]
        or fields.get("Loại chứng khoán:") != "Cổ phiếu"
        or integer(fields.get("Mệnh giá:", ""), "đồng") != registry["par_value_vnd"]
        or integer(fields.get("Số lượng chứng khoán hiện tại:", ""), "Cổ phiếu") != registry["registered_shares"]
    ):
        raise ValueError("VSDC notice and registry disagree on identity or registered balance")
    latest = [r for r in registry["registration_history"] if r["date"] == expected_day]
    matched_change = None
    if len(latest) == 1 and latest[0]["change"]:
        event = latest[0]
        direction = "tăng" if event["change"] > 0 else "giảm"
        amount = fields.get(f"Số lượng chứng khoán {direction}:")
        if (
            amount
            and integer(amount, "Cổ phiếu") == abs(event["change"])
            and fields.get("Lý do:", "").casefold() == event["reason"].casefold()
        ):
            matched_change = event
    if stamp.date().isoformat() != expected_day and matched_change is None:
        raise ValueError("Delayed VSDC notice does not confirm the registration change")
    raw = str(notice)
    return {
        "source_basis": "dated_registration_notice",
        "notice_url": url,
        "notice_published": stamp.isoformat(),
        "notice_registration_change": matched_change,
        "notice_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "analysis_date": asof,
        "scope": "Registered securities confirmed by a dated VSDC notice and reconciled current registration history. Retrieved later, with no later registration change in the observed history. Registered count alone does not establish outstanding shares or free float.",
    }, raw


def load(ticker, asof, cache_dir, *, now=None, session=None):
    ticker = symbol(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = datetime.strptime(asof, "%Y-%m-%d").date()
    path = Path(cache_dir) / "vn-registry" / ticker / (asof + ".json")
    empty = {"ticker": ticker + ".VN", "status": "unavailable", "source_url": ROOT, "scope": SCOPE}
    cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
    if target > now.date():
        return {**empty, "status": "withheld", "reason": "Future registry date"}
    historical = target < now.date()
    if cached:
        observed = datetime.fromisoformat(cached["observed_at"]).astimezone(TZ)
        same_day_vintage = historical and observed.date() <= target
        fresh = 0 <= (now - observed).total_seconds() < 21600
        if same_day_vintage or (fresh and (not historical or cached.get("source_basis") == "dated_registration_notice")):
            return cached
    session = session or requests.Session()
    try:
        url, raw = fetch_registry(ticker, session)
        soup = sanitized_page(raw)
        raw = str(soup)
        record = {
            **parse(raw, ticker, now.date().isoformat() if historical else asof),
            "status": "available",
            "source_url": url,
            "observed_at": now.isoformat(),
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "scope": SCOPE,
        }
        if historical:
            proof, notice_raw = _dated_registration(soup, record, ticker, asof, session)
            record.update(proof)
            if event := proof["notice_registration_change"]:
                issuer_change = _issuer_capital_change(ticker, event, asof, session)
                if issuer_change:
                    record["issuer_capital_change"] = issuer_change
            write_json(
                path.parent / ("raw_" + proof["notice_sha256"] + ".json"),
                {"url": proof["notice_url"], "observed_at": now.isoformat(), "html": notice_raw},
            )
        write_json(
            path.parent / ("raw_" + record["sha256"] + ".json"),
            {"url": url, "observed_at": now.isoformat(), "html": raw},
        )
        write_json(path, record)
        return record
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        if cached:
            return {**cached, "status": "cached_source_error", "reason": type(exc).__name__}
        return {**empty, "status": "withheld" if historical else "unavailable",
                "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}


TRANSFER_SCOPE = (
    "Official VSDC issuer-notice sample. Ownership transfers are separate from "
    "exchange trades, insider buys/sells and CafeF volume totals. A listed update "
    "time is the source's dated public notice, not a guaranteed revision archive."
)
TRANSFER_PAGE_LIMIT = 8
TRANSFER_NOTICE_LIMIT = 24


def _notice_time(node):
    text = node.get_text(" ", strip=True) if node else ""
    match = re.fullmatch(r"Cập nhật ngày (\d{2}/\d{2}/\d{4}) - (\d{2}:\d{2}:\d{2})", text)
    if not match:
        raise ValueError("Invalid VSDC notice date")
    return datetime.strptime(" ".join(match.groups()), "%d/%m/%Y %H:%M:%S").replace(tzinfo=TZ)


def _transfer_title(ticker, title):
    return re.fullmatch(
        re.escape(ticker) + r":\s*(?:Thực hiện )?Chuyển quyền sở hữu(?: ([\d.]+) cổ phiếu)?",
        title, re.I,
    )


def parse_transfer(raw, ticker, published, start, cutoff):
    soup = sanitized_page(raw)
    title = soup.select_one("h3.title-category")
    heading = _transfer_title(ticker, title.get_text(" ", strip=True)) if title else None
    stamp = _notice_time(soup.select_one(".time-newstcph"))
    body = soup.select_one(".content-category")
    if not heading or stamp != published or not start <= stamp <= cutoff or body is None:
        raise ValueError("VSDC notice identity or date differs from issuer list")
    text = " ".join(body.get_text(" ", strip=True).split())
    amount = re.findall(r"thông báo thực hiện chuyển quyền sở hữu ([\d.]+) cổ phiếu", text, re.I)
    date_text = re.findall(r"Ngày hiệu lực chuyển quyền:\s*(\d{2}/\d{2}/\d{4})", text, re.I)
    if len(amount) != 1 or len(date_text) != 1:
        raise ValueError("Missing or ambiguous VSDC transfer amount or date")
    shares = integer(amount[0])
    effective = datetime.strptime(date_text[0], "%d/%m/%Y").date()
    if shares <= 0 or (heading[1] and integer(heading[1]) != shares) or effective > cutoff.date():
        raise ValueError("VSDC transfer title, amount or effective date invalid")
    return {
        "published": stamp.isoformat(), "effective_date": effective.isoformat(),
        "shares": shares, "title": title.get_text(" ", strip=True),
        "sha256": hashlib.sha256(str(soup).encode()).hexdigest(),
    }


def load_transfers(ticker, asof, cache_dir, *, now=None, session=None):
    ticker = symbol(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = datetime.strptime(asof, "%Y-%m-%d").date()
    cutoff = min(now, datetime.combine(target, time.max, TZ))
    start = datetime.combine(target - timedelta(days=365), time.min, TZ)
    path = Path(cache_dir) / "vn-registry" / ticker / (asof + ".transfers.json")
    result = {"scope": TRANSFER_SCOPE, "records": [], "warnings": [], "status": "unavailable",
              "published_window_start": start.date().isoformat()}
    if target > now.date():
        return {**result, "status": "withheld"}
    if path.is_file() and not path.is_symlink():
        cached = json.loads(path.read_text())
        if 0 <= (now - datetime.fromisoformat(cached["observed_at"])).total_seconds() < 21600:
            return cached
    session = session or requests.Session()
    try:
        issuer_url, raw = fetch_registry(ticker, session)
        page = BeautifulSoup(raw, "html.parser")
        # Validate the underlying security, even when its current registration
        # includes events after the requested date.
        parse(str(sanitized_page(raw)), ticker, now.date().isoformat())
        token = page.select_one('meta[name="__VPToken"]')
        headers = {"__VPToken": token["content"]} if token else {}
        listing = page.select_one("#divlistArticlesTCPH_TCDK")
        if listing is None:
            raise ValueError("Missing VSDC issuer notice listing")
        result["source_url"] = issuer_url
        candidates = {}
        previous_stamp = None
        covered = False
        for page_index in range(1, TRANSFER_PAGE_LIMIT + 1):
            links = listing.select("ul.list-news > li")
            if not links and page_index > 1:
                raise ValueError("Unexpected empty VSDC issuer page")
            for item in links:
                stamp = _notice_time(item.select_one(".time-news"))
                if previous_stamp is not None and stamp > previous_stamp:
                    raise ValueError("Unordered VSDC issuer notices")
                previous_stamp = stamp
                if not start <= stamp <= cutoff:
                    continue
                link = item.select_one("h3 a[href]")
                if link and _transfer_title(ticker, link.get_text(" ", strip=True)):
                    href = link["href"]
                    if not re.fullmatch(r"/(?:vi/)?ad/[1-9]\d{0,8}", href):
                        raise ValueError("Invalid VSDC transfer notice link")
                    candidates[ROOT + href] = stamp
            covered = (previous_stamp is not None and previous_stamp < start) or not listing.select_one("#next")
            if covered or page_index == TRANSFER_PAGE_LIMIT:
                break
            fragment = response_text(session.post(
                ROOT + "/isuisser-tcdk/search",
                json={"SearchKey": issuer_url.rsplit("/", 1)[-1],
                      "CurrentPage": page_index + 1, "RecordOnPage": 10},
                headers=headers, timeout=(8, 20),
            ))
            listing = BeautifulSoup(fragment, "html.parser")
        if not covered or len(candidates) > TRANSFER_NOTICE_LIMIT:
            result["warnings"].append("VSDC issuer listing or notice limit reached")
        for url, stamp in list(candidates.items())[:TRANSFER_NOTICE_LIMIT]:
            try:
                raw = response_text(session.get(url, timeout=(8, 20)))
                record = parse_transfer(raw, ticker, stamp, start, cutoff)
                archive = path.parent / ("transfer_" + record["sha256"] + ".json")
                if not archive.exists():
                    write_json(archive, {"url": url, "html": str(sanitized_page(raw))})
                result["records"].append({**record, "url": url})
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                result["warnings"].append("VSDC notice withheld: " + type(exc).__name__)
        result["status"] = "partial" if result["warnings"] else "available"
        result["observed_at"] = now.isoformat()
        write_json(path, result)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        result["warnings"].append("VSDC issuer transfers: " + type(exc).__name__)
    return result
