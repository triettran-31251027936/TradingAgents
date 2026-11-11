"""Published VSDC cash-dividend notices, indexed by shareholder record date."""

from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re
from unicodedata import normalize

import requests
from bs4 import BeautifulSoup

from desktop.share_registry import (
    ROOT, _dated_registration, _notice_time, fetch_registry, integer, parse, response_text, sanitized_page,
)
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


def stock_title(title, ticker):
    return title.startswith(ticker + ":") and bool(re.search(r"cổ tức.*bằng cổ phiếu", title, re.I))


def parse_notice(raw, ticker, isin, record_date, cutoff, *, stock=False):
    page = sanitized_page(raw)
    title = text(page.select_one("h3.title-category"))
    published = _notice_time(page.select_one(".time-newstcph"))
    body = text(page.select_one(".content-category"))
    if published > cutoff:
        return None
    title_matches = stock_title if stock else cash_title
    if not title_matches(title, ticker) or not re.search(rf"Mã ISIN:\s*{re.escape(isin)}\b", body):
        raise ValueError("Dividend issuer identity differs from registry")
    if not re.search(rf"Mã chứng khoán:\s*{re.escape(ticker)}\b", body):
        raise ValueError("Dividend ticker differs from registry")
    record = re.findall(r"Ngày đăng ký cuối cùng:\s*(\d{2}/\d{2}/\d{4})", body)
    if len(record) != 1 or datetime.strptime(record[0], "%d/%m/%Y").date() != record_date:
        raise ValueError("Dividend record date differs from registry listing")
    common = {
        "title": title, "published": published.isoformat(), "record_date": record_date.isoformat(),
        "sha256": hashlib.sha256(str(page).encode()).hexdigest(),
    }
    if stock:
        ratio = re.findall(r"Tỷ lệ thực hiện:\s*(\d+)\s*:\s*(\d+)\b", body, re.I)
        if len(ratio) != 1 or min(map(int, ratio[0])) <= 0:
            raise ValueError("Stock dividend requires an unambiguous positive whole-share ratio")
        held, issued = map(int, ratio[0])
        return {**common, "held": held, "issued": issued}
    payment = re.findall(r"Ngày thanh toán:\s*(\d{2}/\d{2}/\d{4})", body)
    amounts = re.findall(r"\b0?1 cổ phiếu được nhận\s+([\d.]+)\s+đồng\b", body, re.I)
    if len(payment) != 1 or len(amounts) != 1:
        raise ValueError("Missing or ambiguous cash entitlement fields")
    scheduled = datetime.strptime(payment[0], "%d/%m/%Y").date()
    amount = integer(amounts[0])
    if scheduled < record_date or amount <= 0:
        raise ValueError("Cash entitlement contradicts registry date or amount")
    return {
        **common, "scheduled_payment_date": scheduled.isoformat(), "cash_vnd_per_eligible_share": amount,
    }


def load(ticker, asof, cache_dir, *, now=None, session=None):
    ticker = symbol(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = date.fromisoformat(asof)
    start = target - timedelta(days=365)
    cutoff = min(now, datetime.combine(target, time.max, TZ))
    path = Path(cache_dir) / "vn-registry" / ticker / (asof + ".cash-dividends-v2.json")
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
        stock_candidates = {}
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
                title = text(link)
                cash, stock = cash_title(title, ticker), stock_title(title, ticker)
                if start < day <= target and (cash or stock):
                    href = link["href"]
                    if not re.fullmatch(r"/(?:vi/)?ad1?/[1-9]\d{0,8}", href):
                        raise ValueError("Invalid VSDC cash-dividend notice link")
                    url = ROOT + href
                    for bucket, matches in ((candidates, cash), (stock_candidates, stock)):
                        if matches:
                            if url in bucket and bucket[url] != day:
                                raise ValueError("Conflicting VSDC dividend record dates")
                            bucket[url] = day
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
        if result["records"]:
            first_cash = min(row["record_date"] for row in result["records"])
            relevant = [(url, day) for url, day in stock_candidates.items() if day.isoformat() >= first_cash]
            stock_rights = {"records": [], "warnings": []}
            if len(relevant) > MAX_NOTICES:
                stock_rights["warnings"].append("VSDC stock-dividend notice limit reached")
            for url, day in relevant[:MAX_NOTICES]:
                try:
                    raw_notice = response_text(session.get(url, timeout=(8, 20)))
                    notice = parse_notice(raw_notice, ticker, identity["isin"], day, cutoff, stock=True)
                    if notice is not None:
                        stock_rights["records"].append({**notice, "url": url})
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    stock_rights["warnings"].append("VSDC stock-dividend notice withheld: " + type(exc).__name__)
            if stock_rights["records"] and not stock_rights["warnings"]:
                try:
                    balance, _ = _dated_registration(page, identity, ticker, asof, session)
                    if datetime.fromisoformat(balance["notice_published"]) <= cutoff:
                        stock_rights["balance_notice"] = balance
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    stock_rights["warnings"].append("VSDC stock-dividend registration unconfirmed: " + type(exc).__name__)
            result["stock_rights"] = stock_rights
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


def rebase_entitlements(rows, registry, rights, asof):
    first = min(row["record_date"] for row in rows)
    changes = [
        event for event in registry["registration_history"]
        if event["change"] != 0 and first <= event["date"] <= asof
    ]
    notices = rights.get("records", [])
    if rights.get("warnings"):
        raise ValueError("Stock-dividend rights could not be verified")
    if not changes and not notices:
        return sum(row["cash_vnd_per_eligible_share"] for row in rows), None
    if len(changes) != 1 or len(notices) != 1:
        raise ValueError("Intervening registration changes and stock-dividend rights do not match")
    event, = changes
    notice, = notices
    balance = rights.get("balance_notice") or {}
    change = event["change"]
    after = registry["registered_shares"]
    record = notice["record_date"]
    if (
        balance.get("notice_registration_change") != event
        or event.get("reason", "").casefold() != "trả cổ tức bằng cổ phiếu"
        or type(change) is not int or not 0 < change < after
        or not first < record < event["date"] <= balance["notice_published"][:10] <= asof
        or (after - change) * notice["issued"] != change * notice["held"]
        or any(record <= row["record_date"] <= event["date"] for row in rows)
    ):
        raise ValueError("Stock-dividend timing, ratio or registered-share change does not reconcile")
    factor = (after - change) / after
    total = sum(
        row["cash_vnd_per_eligible_share"] * (factor if row["record_date"] < record else 1)
        for row in rows
    )
    return total, factor


def trailing_yield(data, registry, share_proof, close_basis):
    """Price-relative entitlement yield on the reconciled closing share basis."""
    asof = data["asof"]
    rows = data["records"]
    record_dates = [row["record_date"] for row in rows]
    reason = None
    total = None
    factor = None
    close = close_basis.get("observed_close_vnd")
    if data["status"] != "available" or not rows:
        reason = "No complete parsed VSDC cash-entitlement sample"
    elif len(set(record_dates)) != len(record_dates):
        reason = "Multiple notices share a record date; potential amendments or overlapping rights"
    elif any(row["scheduled_payment_date"] > asof for row in rows):
        reason = "At least one published payment is still scheduled after the analysis date"
    elif not share_proof.get("verified") or not registry or not registry.get("registration_history"):
        reason = "Common-share basis is unverified"
    elif close_basis.get("status") != "anchored" or close_basis.get("date") != asof or not close or close <= 0:
        reason = "No independently anchored closing price for the analysis date"
    else:
        try:
            total, factor = rebase_entitlements(rows, registry, data.get("stock_rights", {}), asof)
        except ValueError as exc:
            reason = str(exc)
    formula = (
        "Sum of pre-tax VSDC cash entitlements with record dates in the prior 365 days "
        "and scheduled payment by analysis date / independently anchored closing price. "
        "Requires a stable share basis or a stock dividend reconciled to a VSDC registration notice. "
        "Scheduled entitlements are not proof of receipt or reinvestment."
    )
    if factor is not None:
        formula += f" Pre-event entitlements multiplied by {factor:.10g}; rebased cash total {total:.10g} VND/share."
    return {
        "value": total / close if total is not None else None,
        "unit": "fraction",
        "formula": formula if reason is None else formula + " Withheld: " + reason,
        "source": "VSDC corporate-rights notices and share registry; "
        + (close_basis.get("source") or "CafeF") + " actual session close",
        "asof": asof,
        "status": "available" if total is not None else "missing",
    }
