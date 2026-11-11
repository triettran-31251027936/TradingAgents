"""Published VSDC cash-dividend notices, indexed by shareholder record date."""

from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re
from unicodedata import normalize

import requests
from bs4 import BeautifulSoup

from desktop.share_registry import ROOT, _notice_time, fetch_registry, integer, parse, response_text, sanitized_page
from desktop.vietnam import TZ, symbol, write_json

SCOPE = (
    "Cash entitlements stated in public VSDC notices for record dates in the previous 365 days. "
    "Payment dates are scheduled; later amendments or realized payments are not verified. "
    "Amounts use the shares eligible at each record date, before tax and without corporate-action adjustment."
)
MAX_PAGES = 5
MAX_NOTICES = 12


def text(node):
    return normalize("NFC", " ".join(node.get_text(" ", strip=True).split())) if node else ""


def cash_title(title, ticker):
    return title.startswith(ticker + ":") and bool(re.search(r"cổ tức.*\btiền\b", title, re.I))


def parse_notice(raw, ticker, isin, record_date, cutoff):
    page = sanitized_page(raw)
    title = text(page.select_one("h3.title-category"))
    published = _notice_time(page.select_one(".time-newstcph"))
    body = text(page.select_one(".content-category"))
    if published > cutoff:
        return None
    if not cash_title(title, ticker) or not re.search(rf"Mã ISIN:\s*{re.escape(isin)}\b", body):
        raise ValueError("Dividend issuer identity differs from registry")
    if not re.search(rf"Mã chứng khoán:\s*{re.escape(ticker)}\b", body):
        raise ValueError("Dividend ticker differs from registry")
    record = re.findall(r"Ngày đăng ký cuối cùng:\s*(\d{2}/\d{2}/\d{4})", body)
    payment = re.findall(r"Ngày thanh toán:\s*(\d{2}/\d{2}/\d{4})", body)
    amounts = re.findall(r"\b0?1 cổ phiếu được nhận\s+([\d.]+)\s+đồng\b", body, re.I)
    if len(record) != 1 or len(payment) != 1 or len(amounts) != 1:
        raise ValueError("Missing or ambiguous cash entitlement fields")
    listed = datetime.strptime(record[0], "%d/%m/%Y").date()
    scheduled = datetime.strptime(payment[0], "%d/%m/%Y").date()
    amount = integer(amounts[0])
    if listed != record_date or scheduled < listed or amount <= 0:
        raise ValueError("Cash entitlement contradicts registry date or amount")
    return {
        "title": title, "published": published.isoformat(), "record_date": listed.isoformat(),
        "scheduled_payment_date": scheduled.isoformat(), "cash_vnd_per_eligible_share": amount,
        "sha256": hashlib.sha256(str(page).encode()).hexdigest(),
    }


def load(ticker, asof, cache_dir, *, now=None, session=None):
    ticker = symbol(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = date.fromisoformat(asof)
    start = target - timedelta(days=365)
    cutoff = min(now, datetime.combine(target, time.max, TZ))
    path = Path(cache_dir) / "vn-registry" / ticker / (asof + ".cash-dividends.json")
    result = {"status": "unavailable", "asof": asof, "scope": SCOPE, "records": [],
              "warnings": [], "record_window_start": start.isoformat(), "observed_at": now.isoformat()}
    if target > now.date():
        return {**result, "status": "withheld"}
    if path.is_file() and not path.is_symlink():
        cached = json.loads(path.read_text())
        if 0 <= (now - datetime.fromisoformat(cached["observed_at"])).total_seconds() < 21600:
            return cached
    session = session or requests.Session()
    try:
        issuer_url, raw = fetch_registry(ticker, session)
        identity = parse(raw, ticker, now.date().isoformat())
        result["source_url"] = issuer_url
        page = BeautifulSoup(raw, "html.parser")
        listing = page.select_one("#divlistArticlesTCPH_THQ")
        if listing is None:
            raise ValueError("Missing VSDC corporate-rights listing")
        token = page.select_one('meta[name="__VPToken"]')
        headers = {"__VPToken": token["content"]} if token else {}
        candidates = {}
        for index in range(1, MAX_PAGES + 1):
            table = listing.select_one("table")
            if table is None:
                raise ValueError("Missing VSDC corporate-rights table")
            for row in table.select("tbody tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) != 3:
                    raise ValueError("Invalid VSDC corporate-rights row")
                day = datetime.strptime(text(cells[1]), "%d/%m/%Y").date()
                link = cells[2].select_one("a[href]")
                if start < day <= target and cash_title(text(link), ticker):
                    href = link["href"]
                    if not re.fullmatch(r"/(?:vi/)?ad1?/[1-9]\d{0,8}", href):
                        raise ValueError("Invalid VSDC cash-dividend notice link")
                    url = ROOT + href
                    if url in candidates and candidates[url] != day:
                        raise ValueError("Conflicting VSDC cash-dividend record dates")
                    candidates[url] = day
            if not listing.select_one("#next"):
                break
            if index == MAX_PAGES:
                result["warnings"].append("VSDC corporate-rights page limit reached")
                break
            fragment = response_text(session.post(
                ROOT + "/isuisser-thq/search",
                json={"SearchKey": issuer_url.rsplit("/", 1)[-1], "CurrentPage": index + 1, "RecordOnPage": 10},
                headers=headers, timeout=(8, 20),
            ))
            listing = BeautifulSoup(fragment, "html.parser")
        if len(candidates) > MAX_NOTICES:
            result["warnings"].append("VSDC cash-dividend notice limit reached")
        for url, day in list(candidates.items())[:MAX_NOTICES]:
            try:
                raw = response_text(session.get(url, timeout=(8, 20)))
                notice = parse_notice(raw, ticker, identity["isin"], day, cutoff)
                if notice is not None:
                    result["records"].append({**notice, "url": url})
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                result["warnings"].append("VSDC cash-dividend notice withheld: " + type(exc).__name__)
        result["status"] = "partial" if result["warnings"] else "available" if result["records"] else "empty_sample"
        write_json(path, result)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        result["warnings"].append("VSDC cash-dividend listing unavailable: " + reason)
    return result


def metrics(data):
    rows = data["records"]
    day = max((row["record_date"] for row in rows), default=None)
    latest = [row for row in rows if row["record_date"] == day]
    row = latest[0] if len(latest) == 1 and data["status"] == "available" else None
    return {
        "latest_cash_dividend_per_eligible_share": {
            "value": row["cash_vnd_per_eligible_share"] if row else None,
            "unit": "VND/share",
            "formula": "Latest uniquely identified VSDC cash entitlement per share at its record date, before tax.",
            "source": row["url"] if row else data.get("source_url", ROOT),
            "asof": day or data["asof"],
            "status": "available" if row else "missing",
        }
    }
