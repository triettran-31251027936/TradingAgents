"""Dated official NSO releases; extract only unambiguous period-specific facts.

Do not confuse monthly CPI YoY with nine-month average CPI, or quarterly
underemployment with the nine-month rate in the same article.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, date, datetime, time as day_time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from desktop.company_profile import plain
from desktop.vietnam import write_json

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
API = "https://www.nso.gov.vn/wp-json/wp/v2/posts"
SEARCH = {
    "cpi": "chỉ số giá tiêu dùng",
    "labor": "thông cáo báo chí về tình hình lao động",
    "gdp": "thông cáo báo chí tình hình kinh tế",
}
MONTHS = {
    "một": 1,
    "giêng": 1,
    "hai": 2,
    "ba": 3,
    "tư": 4,
    "bốn": 4,
    "năm": 5,
    "sáu": 6,
    "bảy": 7,
    "tám": 8,
    "chín": 9,
    "mười": 10,
    "mười một": 11,
    "mười hai": 12,
}


def timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing publication/modification date")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def normalize(posts, kind, asof, now):
    cutoff = min(now, datetime.combine(date.fromisoformat(asof), day_time.max, TZ))
    records = []
    if not isinstance(posts, list):
        raise ValueError("Invalid NSO posts schema")
    for post in posts:
        title = plain((post.get("title") or {}).get("rendered", ""))
        eligible = (
            title.lower().startswith("chỉ số giá tiêu dùng")
            if kind == "cpi"
            else bool(re.match(r"^thông cáo báo chí.*tình hình kinh tế", title, re.I))
            if kind == "gdp"
            else title.lower().startswith("thông cáo báo chí") and "lao động" in title.lower()
        )
        if not eligible:
            continue
        try:
            published = timestamp(post.get("date_gmt"))
            modified = timestamp(post.get("modified_gmt"))
        except ValueError:
            continue
        if published > cutoff or modified > cutoff:
            continue
        link = post.get("link", "")
        if not link.startswith("https://www.nso.gov.vn/"):
            continue
        text = (
            plain((post.get("content") or {}).get("rendered", ""))
            .replace("–", "-")
            .replace("−", "-")
        )
        records.append(
            {
                "id": post.get("id"),
                "title": title,
                "published": published.isoformat(),
                "modified": modified.isoformat(),
                "url": link,
                "text": text,
                "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    return sorted(records, key=lambda x: x["published"], reverse=True)


def facts(record, kind):
    title = record["title"].lower()
    body = record["text"]
    years = re.findall(r"năm\s+(20\d{2})", title)
    if not years:
        return {}
    year = int(years[-1])
    result = {}

    def capture(alias, pattern, period, unit="fraction"):
        matches = list(re.finditer(pattern, body, re.I))
        values = []
        for match in matches:
            sign = -1 if match.groupdict().get("direction", "").lower() == "giảm" else 1
            value = float(match["value"].replace(",", ".")) * sign
            if unit == "fraction":
                value /= 100
            values.append(value)
        if not values or len(set(values)) != 1:
            return
        if unit == "fraction" and not -1 <= values[0] <= 1:
            return
        result[alias] = {
            "value": values[0],
            "unit": unit,
            "asof": period,
            "formula": "Official release; exact period-specific statement, not an interpolation or SDMX overwrite",
            "source": record["url"],
            "status": "available",
            "published": record["published"],
            "modified": record["modified"],
            "source_quote": matches[0].group(0),
            "source_content_sha256": record["content_sha256"],
        }

    if kind == "cpi":
        month = re.search(
            r"tháng\s+(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b", title
        )
        if not month:
            return {}
        month_name = month[1]
        period = f"{year:04d}-{MONTHS[month_name]:02d}"
        first = body.split(". ", 1)[0]
        # Both patterns must be within the CPI opening sentence, before gold/USD.
        original = body
        body = first
        capture(
            "vn_release_cpi_mom",
            rf"Chỉ số giá tiêu dùng\s*\(CPI\)\s+tháng\s+{re.escape(month_name)}\s+(?P<direction>tăng|giảm)\s+(?P<value>\d+(?:,\d+)?)%\s+so với tháng trước",
            period,
        )
        capture(
            "vn_release_cpi_yoy",
            r"(?P<direction>tăng|giảm)\s+(?P<value>\d+(?:,\d+)?)%\s+so với cùng kỳ năm trước",
            period,
        )
        body = original
    else:
        quarter = re.search(r"quý\s+(iv|iii|ii|i)\b", title)
        if not quarter:
            return {}
        roman = quarter[1]
        q = {"i": 1, "ii": 2, "iii": 3, "iv": 4}[roman]
        period = f"{year}-Q{q}"
        prefix = rf"quý\s+{roman}/{year}"

        if kind == "gdp":
            capture(
                "vn_release_gdp_yoy",
                rf"Tổng sản phẩm trong nước\s*\(GDP\)\s+{prefix}\s+ước(?:\s+tính)?\s+tăng\s+(?P<value>\d+(?:,\d+)?)%\s+so với cùng kỳ năm trước",
                period,
            )
            return result
        capture(
            "vn_release_unemployment",
            rf"Trong\s+{prefix},\s+tỷ lệ thất nghiệp trong độ tuổi lao động là\s+(?P<value>\d+(?:,\d+)?)%",
            period,
        )
        capture(
            "vn_release_underemployment",
            rf"Tỷ lệ thiếu việc làm trong độ tuổi lao động\s+{prefix}\s+là\s+(?P<value>\d+(?:,\d+)?)%",
            period,
        )
        capture(
            "vn_release_youth_unemployment",
            rf"Tỷ lệ thất nghiệp của thanh niên\s+\(từ 15-24 tuổi\)\s+{prefix}\s+là\s+(?P<value>\d+(?:,\d+)?)%",
            period,
        )
    return result


def load(asof, cache_dir, *, now=None, session=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = date.fromisoformat(asof)
    if target > now.date():
        return {
            "status": "withheld",
            "metrics": {},
            "sources": [],
            "releases": [],
            "warnings": ["Future release query withheld"],
        }
    session = session or requests.Session()
    session.headers.update(
        {"User-Agent": "TradingAgents-Research/0.8", "Accept": "application/json"}
    )
    folder = Path(cache_dir) / "vn-macro" / "releases"
    folder.mkdir(parents=True, exist_ok=True)
    output = {
        "status": "available",
        "metrics": {
            name: {
                "value": None,
                "unit": "fraction",
                "asof": asof,
                "source": API,
                "status": "missing",
                "formula": "Official period-specific release value unavailable; no estimate",
            }
            for name in (
                "vn_release_gdp_yoy",
                "vn_release_cpi_mom",
                "vn_release_cpi_yoy",
                "vn_release_unemployment",
                "vn_release_underemployment",
                "vn_release_youth_unemployment",
            )
        },
        "sources": [],
        "releases": [],
        "warnings": [],
        "scope": "Official dated NSO release statements; may contain estimates, rounded values and later revisions. Not a guaranteed historical vintage archive.",
    }
    cutoff = min(now, datetime.combine(target, day_time.max, TZ))
    for kind, search in SEARCH.items():
        path = folder / (kind + "_" + asof + ".json")
        cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
        status = "available"
        archived = (
            cached
            and target < now.date()
            and timestamp(cached["retrieved_at"]).astimezone(TZ).date() <= target
        )
        if not archived and (
            not cached or now.timestamp() - timestamp(cached["retrieved_at"]).timestamp() >= 21600
        ):
            try:
                time.sleep(0.35)
                response = session.get(
                    API,
                    params={
                        "search": search,
                        "search_columns[]": "post_title",
                        "per_page": 10,
                        "_fields": "id,date_gmt,modified_gmt,link,title,content",
                        "before": cutoff.replace(tzinfo=None).isoformat(),
                    },
                    timeout=(8, 20),
                )
                response.raise_for_status()
                posts = response.json()
                if not isinstance(posts, list):
                    raise ValueError("Invalid official release response")
                cached = {
                    "retrieved_at": now.isoformat(),
                    "posts": posts,
                    "sha256": hashlib.sha256(
                        json.dumps(posts, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest(),
                }
                write_json(path, cached)
            except (requests.RequestException, ValueError) as exc:
                output["warnings"].append(kind + ": " + type(exc).__name__)
                if not cached:
                    continue
                status = "cached_source_error"
        source = {
            "name": "NSO official " + kind + " releases",
            "url": API,
            "retrieved_at": cached["retrieved_at"],
            "sha256": cached["sha256"],
            "status": status,
        }
        output["sources"].append(source)
        try:
            records = normalize(cached["posts"], kind, asof, now)
        except ValueError as exc:
            output["warnings"].append(str(exc))
            continue
        if not records:
            output["warnings"].append(kind + ": no valid dated release")
            continue
        latest = records[0]
        extracted = facts(latest, kind)
        if not extracted:
            output["warnings"].append(kind + ": period-specific values not extracted; no guess")
        output["metrics"].update(extracted)
        output["releases"].append(
            {k: v for k, v in latest.items() if k != "text"}
            | {"extracted_facts": list(extracted), "source_connection_status": status}
        )
    if not output["releases"]:
        output["status"] = "unavailable"
    return output
