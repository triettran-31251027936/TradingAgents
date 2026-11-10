"""CafeF's public shareholder-dealing feed, with explicit result/vintage scope.

The source covers insiders, related parties AND other shareholders. A source
PublishedDate may belong to the initial registration, not the later result.
Keep ESOP/rights separate from market conviction; never infer transaction prices.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from desktop.company_profile import plain
from desktop.vietnam import digest, symbol, write_json

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
URL = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDCoDong.ashx"
PAGE_SIZE = 100
MAX_PAGES = 100
LOCK = threading.Lock()
SCOPE = "CafeF aggregated insider/related-party/major-shareholder dealing records, not an exchange-confirmed transaction ledger. PublishedDate can be an initial plan announcement; it does not establish when a later result became public."
NUMBERS = (
    "VolumeBeforeTransaction",
    "PlanBuyVolume",
    "PlanSellVolume",
    "RealBuyVolume",
    "RealSellVolume",
    "VolumeAfterTransaction",
)
DATES = ("PlanBeginDate", "PlanEndDate", "RealEndDate", "PublishedDate", "OrderDate")
FIELDS = (
    "Stock",
    "TransactionMan",
    "TransactionManPosition",
    "RelatedManPosition",
    "TransactionNote",
    *NUMBERS,
    *DATES,
)
METRICS = {
    "vn_holder_records_announced_365d": "Source shareholder records with an initial/source announcement in trailing365 days",
    "vn_holder_dated_buy_shares_365d": "Reported completed buy shares, execution-end in trailing365 days; all source actor types/mechanisms, includes ESOP/rights, NOT market conviction",
    "vn_holder_dated_sell_shares_365d": "Reported completed sell shares, execution-end in trailing365 days; all source actor types/mechanisms, not a verified market-only sale flow",
    "vn_holder_dated_esop_buy_shares_365d": "Explicitly labelled ESOP completed buys; not open-market buying",
    "vn_holder_unknown_mechanism_results_365d": "Completed source result rows whose mechanism is unspecified; cannot infer open-market activity",
    "vn_insider_dated_buy_shares_365d": "Completed reported buys by explicitly classified corporate officers/directors; includes ESOP/rights and excludes other/unknown actors",
    "vn_insider_dated_sell_shares_365d": "Completed reported sells by explicitly classified corporate officers/directors; mechanism may be unknown",
    "vn_holder_planned_buy_shares_active": "Registered buy shares with future/current plan end and no dated result; plans are not executions",
}


def folded(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFD", str(value)).lower().replace("đ", "d")
        if not unicodedata.combining(c)
    )


def actor(position, related):
    text = folded(position)
    family = text.strip() in (
        "con",
        "vo",
        "chong",
        "me",
        "bo",
        "cha",
        "chi",
        "em",
        "anh",
        "anh trai",
        "em trai",
        "em gai",
        "chi gai",
    )
    if family or "lien quan" in text:
        return "related_party"
    professional = any(
        word in text
        for word in (
            "hdqt",
            "bks",
            "tong gd",
            "tong giam doc",
            "p.tgd",
            "ke toan truong",
            "cong bo thong tin",
            "chairman",
            "chief executive",
            "chief financial",
            "board member",
        )
    )
    if professional:
        return "insider_and_related" if related else "insider"
    if related:
        return "related_party"
    if "co dong lon" in text:
        return "major_shareholder"
    return "other_or_unknown"


def project(row):
    # Work-related dealing fields only. Drop IDs, profile HTML, contact/address fields
    # and names/explicit family relationships of related executives.
    if not isinstance(row, dict):
        raise ValueError("Invalid shareholder record")
    data = {key: row.get(key) for key in FIELDS}
    for key in (
        "Stock",
        "TransactionMan",
        "TransactionManPosition",
        "RelatedManPosition",
        "TransactionNote",
    ):
        if data[key] is not None and not isinstance(data[key], str):
            raise ValueError("Invalid dealing text field")
        data[key] = plain(data[key])
    scope = actor(data["TransactionManPosition"], data["RelatedManPosition"])
    if scope == "related_party":
        data["TransactionManPosition"] = "Người liên quan"
    data["actor_scope"] = scope
    return data


def stamp(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid dealing timestamp")
    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", value)
    if not match:
        raise ValueError("Unexpected dealing timestamp schema")
    millis = int(match[1])
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, TZ)


def quantity(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a share quantity")
    result = float(value)
    if not math.isfinite(result) or result < 0 or not result.is_integer():
        raise ValueError("Invalid share quantity")
    return int(result)


def mechanism(note):
    text = folded(note)
    if "esop" in text:
        return "esop"
    if "quyen" in text:
        return "rights_or_corporate_action"
    if "thoa thuan" in text:
        return "negotiated"
    if "khop lenh" in text and "khong" not in text:
        return "explicit_exchange_matched"
    if any(word in text for word in ("thua ke", "cho tang", "duoc tang")):
        return "gift_or_inheritance"
    return "unspecified"


def normalize(rows, ticker, asof, observed_at):
    target = datetime.strptime(asof, "%Y-%m-%d").date()
    cutoff = min(observed_at, datetime.combine(target, datetime.max.time(), TZ))
    result = []
    issues = []
    future = 0
    for raw in rows:
        try:
            row = project(raw)
            if row["Stock"].upper() != ticker:
                raise ValueError("Ticker mismatch")
            dates = {key: stamp(row.get(key)) for key in DATES}
            nums = {key: quantity(row.get(key)) for key in NUMBERS}
            announced = dates["PublishedDate"]
            if announced and announced > cutoff:
                future += 1
                continue
            begin, end, done = dates["PlanBeginDate"], dates["PlanEndDate"], dates["RealEndDate"]
            window_valid = not (begin and end and begin > end)
            notes = []
            if not window_valid:
                notes.append(
                    "Reversed registration window retained as source error; no date repair; exclude from active-plan sizing"
                )
            if not announced:
                notes.append("Source announcement date unknown")
            if (
                done
                and done <= cutoff
                and nums["RealBuyVolume"] is not None
                and nums["RealSellVolume"] is not None
            ):
                stage = "result_reported"
            elif done and done <= cutoff:
                stage = "result_volume_missing"
                notes.append(
                    "Dated result has missing buy/sell quantity; dated volume totals are incomplete"
                )
            elif done and done > cutoff:
                stage = "future_result_withheld"
                notes.append(
                    "Source result date is after analysis cutoff; reported quantities are not counted in dated result subtotals"
                )
            elif nums["RealBuyVolume"] or nums["RealSellVolume"]:
                stage = "undated_result"
                notes.append(
                    "Reported real volume has no completed date; exclude from dated totals"
                )
            else:
                stage = "registration_or_unresolved"
            if done and announced and done > announced:
                notes.append(
                    "PublishedDate precedes completion; it cannot date publication of the completed result"
                )
            before, after, buy, sell = (
                nums[k]
                for k in (
                    "VolumeBeforeTransaction",
                    "VolumeAfterTransaction",
                    "RealBuyVolume",
                    "RealSellVolume",
                )
            )
            if (
                stage == "result_reported"
                and all(v is not None for v in (before, after, buy, sell))
                and before + buy - sell != after
            ):
                notes.append(
                    "Before + reported buy - reported sell differs from after; corporate actions/other changes not reconciled"
                )
            record = {
                "id": digest(row),
                "ticker": ticker + ".VN",
                "party": row["TransactionMan"],
                "role": row["TransactionManPosition"],
                "actor_scope": row["actor_scope"],
                "related_insider_role": row["RelatedManPosition"],
                "mechanism": mechanism(row["TransactionNote"]),
                "note": row["TransactionNote"],
                "source_announcement_date": announced.date().isoformat() if announced else None,
                "plan_begin": begin.date().isoformat() if begin else None,
                "plan_end": end.date().isoformat() if end else None,
                "plan_window_valid": window_valid,
                "reported_result_end": done.date().isoformat() if done else None,
                "stage": stage,
                "planned_buy": nums["PlanBuyVolume"],
                "planned_sell": nums["PlanSellVolume"],
                "reported_buy": buy,
                "reported_sell": sell,
                "holding_before": before,
                "holding_after": after,
                "transaction_price": None,
                "quality_notes": notes,
            }
            result.append(record)
        except (ValueError, TypeError, OverflowError, OSError, AttributeError) as exc:
            issues.append(type(exc).__name__ + ": " + str(exc))
    result.sort(
        key=lambda row: (
            row["source_announcement_date"] or "",
            row["reported_result_end"] or "",
            row["id"],
        ),
        reverse=True,
    )
    return result, issues, future


def page_data(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("Data"), dict):
        raise ValueError("Unexpected CafeF dealing response")
    data = raw["Data"]
    total = data.get("TotalCount")
    rows = data.get("Data")
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise ValueError("Invalid dealing pagination schema")
    return total, rows


def load(ticker, asof, cache_dir, *, now=None, session=None):
    name = symbol(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = datetime.strptime(asof, "%Y-%m-%d").date()
    folder = Path(cache_dir) / "vn-insider" / name
    folder.mkdir(parents=True, exist_ok=True)
    base = {
        "ticker": name + ".VN",
        "asof": asof,
        "source_url": URL,
        "source_page": f"https://cafef.vn/du-lieu/Lich-su-giao-dich-{name.lower()}-6.chn",
        "scope": SCOPE,
        "records": [],
        "warnings": [],
    }
    if target > now.date():
        return {**base, "status": "withheld", "reason": "Future analysis date"}
    with LOCK:
        path = folder / "latest.json"
        cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
        connection_error = None
        if target < now.date():
            candidates = []
            for p in folder.glob("*.snapshot.json"):
                if p.is_symlink():
                    continue
                record = json.loads(p.read_text())
                if (
                    datetime.fromisoformat(record["first_observed_at"]).astimezone(TZ).date()
                    <= target
                ):
                    candidates.append(record)
            if not candidates:
                return {
                    **base,
                    "status": "withheld",
                    "reason": "No locally observed dealing vintage by analysis date; current rows may contain later results under earlier plan announcement dates",
                }
            cached = max(candidates, key=lambda x: x["first_observed_at"])
        elif (
            not cached
            or now.timestamp() - datetime.fromisoformat(cached["retrieved_at"]).timestamp() >= 3600
        ):
            session = session or requests.Session()
            session.headers.update(
                {
                    "User-Agent": "TradingAgents-Research/0.8",
                    "Accept": "application/json, text/plain, */*",
                }
            )
            rows = []
            hashes = []
            expected = None
            pagination_issues = []
            seen = set()
            pages = 0
            try:
                for page in range(1, MAX_PAGES + 1):
                    time.sleep(0.35)
                    response = session.get(
                        URL,
                        params={
                            "Symbol": name,
                            "StartDate": "",
                            "EndDate": target.strftime("%m/%d/%Y"),
                            "PageIndex": page,
                            "PageSize": PAGE_SIZE,
                        },
                        timeout=(8, 25),
                    )
                    response.raise_for_status()
                    raw = response.json()
                    hashes.append(digest(raw))
                    total, items = page_data(raw)
                    pages = page
                    if expected is None:
                        expected = total
                    elif total != expected:
                        pagination_issues.append("Source total changed during pagination")
                    for item in items:
                        selected = project(item)
                        key = digest(selected)
                        if key in seen:
                            pagination_issues.append(
                                "Duplicate record/page boundary drift; coverage not proven"
                            )
                            continue
                        seen.add(key)
                        rows.append(selected)
                    if page * PAGE_SIZE >= total:
                        break
                    if not items:
                        pagination_issues.append("Empty page before declared end")
                        break
                if len(rows) != expected:
                    pagination_issues.append("Retrieved unique count differs from source total")
                version = digest(
                    {
                        "rows": sorted(rows, key=digest),
                        "total": expected,
                        "issues": pagination_issues,
                    }
                )
                snapshot = folder / (version + ".snapshot.json")
                old = (
                    json.loads(snapshot.read_text())
                    if snapshot.is_file() and not snapshot.is_symlink()
                    else None
                )
                cached = {
                    "version": version,
                    "rows": rows,
                    "source_total": expected,
                    "page_count": pages,
                    "page_hashes": hashes,
                    "pagination_issues": pagination_issues,
                    "retrieved_at": now.isoformat(),
                    "first_observed_at": old["first_observed_at"] if old else now.isoformat(),
                }
                write_json(snapshot, cached)
                write_json(path, cached)
            except (requests.RequestException, ValueError) as exc:
                if not cached:
                    return {
                        **base,
                        "status": "unavailable",
                        "reason": type(exc).__name__,
                        "warnings": [
                            "Source was not retrieved; missing coverage is not no activity"
                        ],
                    }
                connection_error = type(exc).__name__
        records, issues, future = normalize(cached["rows"], name, asof, now)
        warnings = [*cached["pagination_issues"], *issues]
        if future:
            warnings.append(f"{future} rows announced after cutoff withheld")
        if connection_error:
            warnings.append("Using cached source after " + connection_error)
        invalid_windows = sum(not row["plan_window_valid"] for row in records)
        if invalid_windows:
            warnings.append(
                f"{invalid_windows} source registration windows are reversed; retained, not repaired; result fields evaluated separately"
            )
        status = (
            "partial"
            if cached["pagination_issues"] or issues
            else "cached_source_error"
            if connection_error
            else "available"
        )
        return {
            **base,
            "status": status,
            "records": records,
            "warnings": warnings,
            "source_total": cached["source_total"],
            "retrieved_count": len(cached["rows"]),
            "page_count": cached["page_count"],
            "retrieved_at": cached["retrieved_at"],
            "first_observed_at": cached["first_observed_at"],
            "source_sha256": cached["version"],
            "page_hashes": cached["page_hashes"],
            "historical_scope": "Exact locally observed snapshot only; source announcement date is not proof of historical result availability",
        }


# Published result pages of known transfers, selected independently of the
# aggregate feed. The dated evidence is verified when it is retrieved.
HISTORICAL_RESULTS = {
    "VHM.VN": {
        ("2026-04-07", 59001762): (
            "2026-04-13",
            "https://cafef.vn/du-lieu/vhm-2665267/vhm-tap-doan-vingroup-cong-ty-co-phan-da-chuyen-quyen-so-huu-59001762-cp.chn",
        ),
        ("2026-06-25", 15226260): (
            "2026-06-26",
            "https://cafef.vn/du-lieu/vhm-2917763/vhm-tap-doan-vingroupctcp-dang-ky-chuyen-quyen-so-huu-15226260-cp.chn",
        ),
        ("2026-08-05", 4806290): (
            "2026-08-10",
            "https://cafef.vn/du-lieu/vhm-2949586/vhm-tap-doan-vingroupctcp-da-chuyen-quyen-so-huu-4806290-cp.chn",
        ),
    }
}


def verify_result_page(response, url, ticker, published, shares):
    response.raise_for_status()
    if response.url != url or len(response.content) > 2_000_000:
        raise ValueError("Unexpected CafeF disclosure URL or size")
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.select_one('meta[property="og:title"]')
    stamp = soup.select_one("[id$=NewsContent1_lblNgay]")
    body = soup.select_one("#newscontent .pa-hose")
    origin = soup.select_one("#originSource a")
    attachment = soup.select_one("#newscontent a.file")
    rendered = title.get("content", "") if title else ""
    day = datetime.fromisoformat(published).strftime("%d/%m/%Y")
    if (
        not rendered.startswith(symbol(ticker) + ":")
        or not re.search(r"(?<![\d.])" + re.escape(f"{shares:,}".replace(",", ".")) + r"(?![\d.])", rendered)
        or not stamp or day not in stamp.get_text(" ", strip=True)
        or not body or "bao cao ket qua giao dich co phieu" not in folded(body.get_text(" ", strip=True))
        or "vingroup" not in folded(body.get_text(" ", strip=True))
        or "co lien quan" not in folded(body.get_text(" ", strip=True))
        or not origin or origin.get_text(strip=True) != "HOSE"
        or urlparse(origin.get("href", "")).hostname not in ("hsx.vn", "www.hsx.vn")
        or not attachment or "ket qua giao dich" not in folded(attachment.get_text(" ", strip=True))
    ):
        raise ValueError("CafeF result evidence does not match ticker, actor, quantity or publication date")
    pdf = urlparse(attachment.get("href", ""))
    if pdf.scheme != "https" or pdf.hostname != "cafef1.mediacdn.vn" or not pdf.path.endswith(".pdf"):
        raise ValueError("Invalid disclosure attachment link")
    return {"url": url, "published": published, "attachment": pdf.geturl(),
            "sha256": digest({"title": rendered, "date": stamp.get_text(" ", strip=True),
                              "body": body.get_text(" ", strip=True), "attachment": pdf.geturl()})}


def corroborate_historical(bundle, transfers, cache_dir, *, now=None, session=None):
    ticker, asof = bundle["ticker"], bundle["asof"]
    catalog = HISTORICAL_RESULTS.get(ticker)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if not catalog or bundle["status"] != "withheld" or transfers.get("status") != "available":
        return bundle
    if asof >= now.date().isoformat():
        return bundle
    start = (datetime.fromisoformat(asof).date() - timedelta(days=365)).isoformat()
    if not any(start <= end <= asof for end, _ in catalog):
        return bundle
    session = session or requests.Session()
    current = load(ticker, now.date().isoformat(), cache_dir, now=now, session=session)
    if current["status"] != "available":
        return bundle
    rows, evidence, warnings = [], [], []
    candidates = [
        row for row in current["records"]
        if (
            start <= (row["reported_result_end"] or "") <= asof
            or row["stage"] == "undated_result" and start <= (row["source_announcement_date"] or "") <= asof
        )
    ]
    expected = {(end, shares) for end, shares in catalog if start <= end <= asof}
    if {(row["reported_result_end"], row["reported_sell"]) for row in candidates} != expected or len(candidates) != len(expected):
        warnings.append("Later-observed CafeF result rows do not all match the dated result catalog")
    for row in candidates:
        disclosure = catalog.get((row["reported_result_end"], row["reported_sell"]))
        if not disclosure:
            continue
        published, url = disclosure
        if published > asof:
            continue
        related = [
            item for item in transfers["records"]
            if row["plan_begin"] and row["plan_begin"] <= item["effective_date"] <= row["reported_result_end"]
            and item["published"][:10] <= published
        ]
        if (
            row["stage"] != "result_reported" or row["reported_buy"] != 0
            or folded(row["party"]) != "tap doan vingroup - cong ty co phan"
            or folded(row["note"]) != "chuyen quyen so huu"
            or sum(item["shares"] for item in related) != row["reported_sell"]
        ):
            warnings.append("CafeF row and dated VSDC transfer amounts did not reconcile")
            continue
        try:
            report = verify_result_page(session.get(url, timeout=(8, 20)), url, ticker, published, row["reported_sell"])
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            warnings.append("Dated result evidence: " + type(exc).__name__)
            continue
        evidence.append({**report, "observed_at": now.isoformat(), "vsdc_sources": [item["url"] for item in related]})
        rows.append({
            **row, "role": "Tổ chức có liên quan đến người nội bộ", "actor_scope": "related_party",
            "mechanism": "ownership_transfer", "result_published": published, "result_url": url,
            "source_announcement_date": None, "plan_begin": None, "plan_end": None,
            "related_insider_role": None, "plan_window_valid": True,
            "planned_buy": None, "planned_sell": None, "holding_before": None, "holding_after": None,
            "quality_notes": ["Dated CafeF report attributed to HOSE; quantity matched to VSDC transfer notices. No exchange execution or price established."],
        })
    if not rows:
        return {**bundle, "warnings": bundle["warnings"] + warnings}
    return {
        **bundle, "records": rows, "result_evidence": evidence, "retrieved_at": current["retrieved_at"],
        "status": "partial" if warnings else "corroborated_results",
        "warnings": bundle["warnings"] + warnings,
        "scope": "Dated CafeF result pages attributed to HOSE and matched to VSDC ownership transfer notices. Totals cover only reconciled results in the later-observed CafeF feed; registered plans and full historical coverage remain unverified.",
    }


def metrics(bundle):
    asof = bundle["asof"]
    start = (datetime.strptime(asof, "%Y-%m-%d").date() - timedelta(days=365)).isoformat()
    usable = bundle["status"] in ("available", "cached_source_error")
    rows = bundle.get("records", [])
    results = [
        r
        for r in rows
        if r["stage"] == "result_reported" and start <= r["reported_result_end"] <= asof
    ]
    announced = [
        r
        for r in rows
        if r["source_announcement_date"] and start <= r["source_announcement_date"] <= asof
    ]
    plans = [
        r
        for r in rows
        if r["stage"] == "registration_or_unresolved"
        and r["plan_end"]
        and r["plan_end"] >= asof
        and r["planned_buy"] is not None
        and r["plan_window_valid"]
    ]
    corroborated = bundle["status"] == "corroborated_results"
    result_totals_reliable = (usable or corroborated) and not any(
        (
            r["stage"] == "result_volume_missing"
            and r["reported_result_end"]
            and start <= r["reported_result_end"] <= asof
        )
        for r in rows
    )
    plan_totals_reliable = not any(
        r["stage"] == "registration_or_unresolved"
        and not r["plan_window_valid"]
        and max(r["plan_begin"] or "", r["plan_end"] or "") >= asof
        for r in rows
    )
    values = [
        len(announced),
        sum(r["reported_buy"] for r in results),
        sum(r["reported_sell"] for r in results),
        sum(r["reported_buy"] for r in results if r["mechanism"] == "esop"),
        sum(r["mechanism"] == "unspecified" for r in results),
        sum(
            r["reported_buy"]
            for r in results
            if r["actor_scope"] in ("insider", "insider_and_related")
        ),
        sum(
            r["reported_sell"]
            for r in results
            if r["actor_scope"] in ("insider", "insider_and_related")
        ),
        sum(r["planned_buy"] for r in plans),
    ]
    output = {}
    for (key, definition), value in zip(METRICS.items(), values, strict=True):
        if key == "vn_holder_records_announced_365d":
            available = usable
        elif key == "vn_holder_planned_buy_shares_active":
            available = usable and plan_totals_reliable
        else:
            available = result_totals_reliable
        scope = (
            "; dated corroborated result subset from the later-observed feed; includes ownership transfers, separate from exchange trades"
            if corroborated
            else "; within retrieved source records only; announcement date is not result-publication date; undated results excluded without assigning them to another period; not a complete market census"
        )
        output[key] = {
            "value": value if available else None,
            "unit": "records"
            if key in ("vn_holder_records_announced_365d", "vn_holder_unknown_mechanism_results_365d")
            else "shares",
            "asof": asof,
            "formula": definition + scope,
            "source": bundle["source_page"],
            "status": "available" if available else "missing",
        }
    return output


def summary(bundle):
    return {
        key: value for key, value in bundle.items() if key not in ("records", "page_hashes")
    } | {
        "record_count": len(bundle.get("records", [])),
        "undated_result_count": sum(
            r["stage"] == "undated_result" for r in bundle.get("records", [])
        ),
        "unknown_actor_count": sum(
            r["actor_scope"] == "other_or_unknown" for r in bundle.get("records", [])
        ),
        "recent_examples": bundle.get("records", [])[:5],
        "metrics": metrics(bundle),
        "context_scope": "Compact summary/examples; original get_insider_transactions tool returns every retained record as CSV, with no silent row truncation",
    }


def tool(bundle, ticker, asof):
    if symbol(ticker) + ".VN" != bundle["ticker"] or asof != bundle["asof"]:
        raise ValueError("Insider request differs from canonical ticker/date")
    output = io.StringIO()
    output.write(
        json.dumps(
            {key: value for key, value in summary(bundle).items() if key != "recent_examples"},
            ensure_ascii=False,
        )
        + "\n"
    )
    fields = (
        "party",
        "role",
        "actor_scope",
        "mechanism",
        "source_announcement_date",
        "plan_begin",
        "plan_end",
        "plan_window_valid",
        "stage",
        "planned_buy",
        "planned_sell",
        "reported_buy",
        "reported_sell",
        "reported_result_end",
        "result_published",
        "result_url",
        "holding_before",
        "holding_after",
        "note",
        "quality_notes",
    )
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in bundle.get("records", []):
        writer.writerow(
            [
                json.dumps(row.get(key), ensure_ascii=False)
                if isinstance(row.get(key), list)
                else row.get(key)
                for key in fields
            ]
        )
    return output.getvalue()
