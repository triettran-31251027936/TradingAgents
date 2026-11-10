"""Dated issuer disclosures for PVS, with explicit correction handling."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from desktop.insider_vietnam import actor
from desktop.vietnam import TZ, digest

PAGE = "https://www.ptsc.com.vn/co-dong/giao-dich-cua-nguoi-noi-bo-nguoi-co-lien-quan"
DAY = r"\d{1,2}/\d{1,2}/\d{4}"
COUNT = r"(?:\d{1,3}(?:\.\d{3})+|\d+)"
SCOPE = (
    "Dated PTSC issuer-published PVS insider disclosures from the first public index page. "
    "The validated subset is not a census of shareholders, trades or orders. "
    "Plans do not establish fills or execution method. Earlier revisions may be missing."
)


def _match(pattern, text):
    matches = re.findall(pattern, text, flags=re.I)
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous issuer disclosure field")
    return matches[0]


def _iso(day, fmt="%d/%m/%Y"):
    return datetime.strptime(day, fmt).date().isoformat()


def _label(text, label, value):
    return _match(r"(?:^(?:- )?| - )" + label + r"(?:/[^:]+)?\s*:\s*" + value, text)


def _qty(text, label):
    return int(_label(text, label, f"({COUNT})(?![.\\d])").replace(".", ""))


def _document(session, url):
    response = session.get(url, timeout=(5, 10))
    response.raise_for_status()
    if response.url != url or len(response.content) > 1_000_000:
        raise ValueError("Unexpected PTSC disclosure URL or size")
    return BeautifulSoup(response.content, "html.parser"), hashlib.sha256(response.content).hexdigest()


def _parse(doc, published, url):
    heading = doc.select_one(".heading:has(h1)")
    if not heading or _iso(heading.select_one(".sub-title time").get_text(strip=True)) != published:
        raise ValueError("Issuer index and article publication dates differ")
    body = heading.find_next_sibling("div", class_="boding")
    text = " ".join(body.get_text(" ", strip=True).split())
    title = heading.select_one("h1").get_text(" ", strip=True)
    if title.casefold().startswith("đính chính kết quả"):
        party = _match(rf"cổ phiếu ngày {DAY} của Ông (.+?) [–-]", text)
        original_date = _iso(_match(rf"cổ phiếu ngày ({DAY}) của Ông", text))
        if _label(text, "Loại giao dịch đã thực hiện", r"(bán)\b").casefold() != "bán":
            raise ValueError("Unsupported issuer correction")
        return {
            "party": party, "published": published, "url": url, "original_date": original_date,
            "reported_sell": _qty(text, "Số lượng cổ phiếu đã giao dịch"),
            "reported_result_end": _iso(_match(rf"cổ phiếu PVS tôi sở hữu sau ngày ({DAY})", text)),
            "dividend_shares": int(_match(rf"({COUNT}) CP được phân bổ thêm", text).replace(".", "")),
        }

    if _label(text, "Mã chứng khoán", r"([A-Z]{3})\b") != "PVS":
        raise ValueError("Issuer article does not identify PVS")
    party = _label(text, "Tên cá nhân thực hiện giao dịch", r"([^/]+?)(?=\s*(?:/| - ))")
    role = _label(text, "Chức vụ hiện nay tại [^/:]+", r"([^/]+?)(?=\s*(?:/| - ))")
    scope = actor(role, "")
    if scope != "insider":
        raise ValueError("Issuer article does not identify a supported insider role")
    direction = _match(r"Số lượng cổ phiếu đăng ký (mua|bán)\b", text).casefold()
    side = "buy" if direction == "mua" else "sell"
    planned = _qty(text, "Số lượng cổ phiếu đăng ký " + direction)
    result = bool(re.search(r"Số lượng cổ phiếu đã (mua|bán)\b", text, flags=re.I))
    prefix = "Ngày " if result else "Ngày dự kiến "
    begin = _iso(_label(text, prefix + "bắt đầu giao dịch", f"({DAY})"))
    end = _iso(_label(text, prefix + "kết thúc giao dịch", f"({DAY})"))
    if begin > end or (result and end > published) or (not result and published > begin):
        raise ValueError("Issuer transaction dates are inconsistent")
    before = _qty(text, "Số lượng cổ phiếu nắm giữ trước khi thực hiện giao dịch")
    row = {
        "party": party, "role": role, "actor_scope": scope, "mechanism": "unspecified",
        "source_announcement_date": published, "source_url": url, "holding_before": before,
        "planned_buy": planned if side == "buy" else 0, "planned_sell": planned if side == "sell" else 0,
        "plan_window_valid": True, "stage": "result_reported" if result else "registration_or_unresolved",
    }
    if result:
        executed = _qty(text, "Số lượng cổ phiếu đã " + direction)
        after = _qty(text, "Số lượng cổ phiếu nắm giữ sau khi thực hiện giao dịch")
        if executed > planned or before + (executed if side == "buy" else -executed) != after:
            raise ValueError("Issuer quantity and holdings do not reconcile")
        row.update(
            reported_buy=executed if side == "buy" else 0, reported_sell=executed if side == "sell" else 0,
            holding_after=after, reported_result_end=end, result_published=published, result_url=url,
        )
    else:
        row.update(plan_begin=begin, plan_end=end)
    return row


def _apply_correction(rows, correction):
    candidates = [
        row for row in rows
        if row["stage"] == "result_reported" and row["party"] == correction["party"]
        and row["reported_result_end"] == correction["reported_result_end"]
        and correction["original_date"] <= row["result_published"] < correction["published"]
        and row["reported_sell"] > 0
    ]
    row, = candidates
    quantity = correction["reported_sell"]
    if quantity > row["planned_sell"] or (
        row["holding_before"] - quantity + correction["dividend_shares"] != row["holding_after"]
    ):
        raise ValueError("Issuer correction does not reconcile")
    row.update(
        original_reported_sell=row["reported_sell"],
        original_result_published=row["result_published"], original_result_url=row["result_url"],
        reported_sell=quantity, result_published=correction["published"], result_url=correction["url"],
        quality_notes=["PTSC correction supersedes original sell quantity. Dividend shares reconcile holdings and are not counted as buys."],
    )


def load(asof, *, now=None, session=None):
    now = now or datetime.now(TZ)
    target = date.fromisoformat(asof)
    if target > now.astimezone(TZ).date():
        raise ValueError("Future PTSC disclosure date")
    start = (target - timedelta(days=365)).isoformat()
    session = session or requests.Session()
    catalog, index_hash = _document(session, PAGE)
    rows, sources, corrections, warnings = [], [], [], []
    seen = set()
    unknown_correction = False
    for card in catalog.select(".news-card-item"):
        link, date_tag = card.select_one(".title a[href]"), card.select_one("time")
        if not link or not date_tag:
            continue
        url = link["href"]
        published = _iso(date_tag.get_text(strip=True), "%d.%m.%Y")
        if not start <= published <= asof or url in seen:
            continue
        parsed = urlparse(url)
        if not url.startswith(PAGE + "/") or parsed.query or parsed.fragment:
            raise ValueError("Unexpected PTSC article path")
        seen.add(url)
        if len(seen) > 16:
            warnings.append("PTSC index exceeded the bounded article limit")
            break
        try:
            article, sha = _document(session, url)
            row = _parse(article, published, url)
        except (ValueError, AttributeError, requests.RequestException):
            warnings.append("Unvalidated PTSC article: " + url)
            if link.get_text(" ", strip=True).casefold().startswith("đính chính"):
                unknown_correction = True
            continue
        (corrections if "original_date" in row else rows).append(row)
        sources.append({"url": url, "published": published, "sha256": sha, "retrieved_at": now.isoformat()})
    for correction in sorted(corrections, key=lambda row: row["published"]):
        try:
            _apply_correction(rows, correction)
        except ValueError:
            # An unresolved correction can make a previously published quantity stale.
            rows = [row for row in rows if row["party"] != correction["party"] or row["stage"] != "result_reported"]
            warnings.append("Unresolved PTSC correction: " + correction["url"])
    if unknown_correction:
        rows = [row for row in rows if row["stage"] != "result_reported"]
    for row in rows:
        row["id"] = digest(row)
    return {
        "ticker": "PVS.VN", "asof": asof, "source_page": PAGE, "source_url": PAGE,
        "source_sha256": index_hash, "retrieved_at": now.isoformat(), "scope": SCOPE,
        "status": "issuer_results" if any(row["stage"] == "result_reported" for row in rows) else "withheld",
        "records": rows, "issuer_sources": sources, "warnings": warnings,
    }
