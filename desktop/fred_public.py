"""Official public FRED CSV for live VN analysis when no API key is present.

Locally observed same-day downloads may be replayed later, with their original
observation timestamps. They are never represented as ALFRED vintages. The
original keyed FRED transport remains authoritative whenever a key is supplied.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from desktop import vietnam
from tradingagents.dataflows.vendors import fred

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
META_URL = "https://fred.stlouisfed.org/series/"
MAX_BYTES = 2 * 1024 * 1024
LOCK = threading.Lock()
SCOPE = (
    "Official FRED public download, latest revision observed now; not an ALFRED "
    "historical vintage. Observation dates name measured periods, not release dates. "
    "An index level is not an inflation rate. US/euro series retain their original "
    "country semantics; they are not Vietnamese substitutes."
)


def download(url, params):
    with requests.get(url, params=params, timeout=(8, 25), stream=True) as response:
        response.raise_for_status()
        body = bytearray()
        for part in response.iter_content(65536):
            body.extend(part)
            if len(body) > MAX_BYTES:
                raise ValueError("FRED public response exceeds the bounded download size")
        return bytes(body)


def metadata(raw, series):
    soup = BeautifulSoup(raw, "html.parser")
    datasets = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        datasets.extend(value if isinstance(value, list) else [value])
    dataset = next(
        (
            row
            for row in datasets
            if isinstance(row, dict)
            and row.get("@type") == "Dataset"
            and row.get("alternateName") == series
        ),
        None,
    )
    units = soup.select_one(".series-meta-value-units")
    frequency = soup.select_one(".series-meta-value-frequency")
    if not dataset or not units or not frequency or not dataset.get("name"):
        raise ValueError("FRED public metadata identity/units/frequency could not be verified")
    modified = datetime.fromisoformat(dataset["dateModified"].replace("Z", "+00:00"))
    if modified.tzinfo is None:
        raise ValueError("FRED metadata update time has no timezone")
    unit_block = units.find_parent(class_="meta-col")
    unit_description = unit_block.get_text(" ", strip=True) if unit_block else None
    return {
        "series_id": series,
        "title": dataset["name"],
        "units_and_adjustment": unit_description or "Source adjustment metadata unavailable",
        "units": units.get_text(" ", strip=True),
        "frequency": frequency.get_text(" ", strip=True),
        "source_last_updated": modified.isoformat(),
    }


def observations(raw, series, start, end):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != ["observation_date", series]:
        raise ValueError("FRED CSV series identity/schema mismatch")
    rows, seen = [], set()
    for item in reader:
        day, value = item["observation_date"], item[series]
        if date.fromisoformat(day).isoformat() != day or day in seen:
            raise ValueError("Invalid or duplicate FRED observation date")
        seen.add(day)
        if value in ("", "."):
            continue
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Nonfinite FRED observation")
        if start <= day <= end:
            rows.append({"date": day, "value": value})
    return sorted(rows, key=lambda row: row["date"])


def observed_vintage(path, series, asof, start):
    """Replay only a complete public download observed on the requested VN day."""
    if not path.is_file() or path.is_symlink():
        return None
    try:
        bundle = json.loads(path.read_text())
        checksum = bundle.pop("content_sha256", None)
        if checksum != vietnam.digest(bundle):
            return None
        observed = datetime.fromisoformat(bundle["observed_at"])
        updated = datetime.fromisoformat(bundle["source_last_updated"])
        if (
            observed.tzinfo is None
            or updated.tzinfo is None
            or observed.astimezone(vietnam.TZ).date().isoformat() != asof
            or updated > observed
            or bundle["series_id"] != series
            or bundle["asof"] != asof
            or bundle["window_start"] != start
            or bundle["version"] != 1
        ):
            return None
        if observations(bundle["raw_csv"].encode(), series, start, asof) != bundle["observations"]:
            return None
        sources = bundle["sources"]
        if (
            len(sources) != 2
            or sources[0]["url"] != META_URL + series
            or sources[1]["url"] != CSV_URL
            or sources[1]["params"]["id"] != series
            or sources[1]["params"]["cosd"] != start
            or not start <= sources[1]["params"]["coed"] <= asof
            or any(not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) for row in sources)
            or sources[1]["sha256"]
            not in {
                hashlib.sha256(bundle["raw_csv"].encode(encoding)).hexdigest()
                for encoding in ("utf-8", "utf-8-sig")
            }
        ):
            return None
        return bundle
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return None


def get_macro_data(indicator, asof, look_back_days, cache_dir):
    if os.environ.get("FRED_API_KEY"):
        return fred.get_macro_data(indicator, asof, look_back_days)
    series = fred._resolve_series_id(indicator)
    if not re.fullmatch(r"[A-Z0-9_]{1,30}", series):
        raise ValueError("Invalid public FRED series ID")
    days = fred.DEFAULT_LOOKBACK_DAYS if look_back_days is None else look_back_days
    if type(days) is not int or not 1 <= days <= 36500:
        raise ValueError("FRED public lookback must be 1..36500 days")
    end = date.fromisoformat(asof)
    if end.isoformat() != asof:
        raise ValueError("Invalid analysis date")
    start = (end - timedelta(days=days)).isoformat()
    today = datetime.now(vietnam.TZ).date().isoformat()
    historical = asof < today
    path = Path(cache_dir) / "fred-public" / f"{series}_{asof}_{days}.json"
    if asof != today:
        # No new request can establish what was available on a past day.
        bundle = observed_vintage(path, series, asof, start) if historical else None
        if bundle is None:
            return (
                "DATA_UNAVAILABLE: FRED API key/ALFRED vintage or a verified locally "
                "observed same-day snapshot is required for this historical analysis. "
                "Public latest-revision CSV was not requested or backdated."
            )
    public_end = min(asof, fred._fred_today())
    observed = datetime.now(UTC)
    with LOCK:
        bundle = bundle if historical else None
        if not historical and path.is_file() and not path.is_symlink():
            cached = json.loads(path.read_text())
            checksum = cached.pop("content_sha256", None)
            age = (observed - datetime.fromisoformat(cached["observed_at"])).total_seconds()
            if (
                checksum == vietnam.digest(cached)
                and 0 <= age < 900
                and cached["series_id"] == series
                and cached["asof"] == asof
                and cached["window_start"] == start
            ):
                bundle = cached
        if bundle is None:
            meta_raw = download(META_URL + series, {})
            info = metadata(meta_raw, series)
            csv_raw = download(CSV_URL, {"id": series, "cosd": start, "coed": public_end})
            rows = observations(csv_raw, series, start, public_end)
            bundle = {
                **info,
                "version": 1,
                "asof": asof,
                "window_start": start,
                "observed_at": datetime.now(UTC).isoformat(),
                "scope": SCOPE,
                "observations": rows,
                "raw_csv": csv_raw.decode("utf-8-sig"),
                "sources": [
                    {"url": META_URL + series, "sha256": hashlib.sha256(meta_raw).hexdigest()},
                    {
                        "url": CSV_URL,
                        "params": {
                            "id": series,
                            "cosd": start,
                            "coed": public_end,
                        },
                        "sha256": hashlib.sha256(csv_raw).hexdigest(),
                    },
                ],
            }
            vietnam.write_json(path, {**bundle, "content_sha256": vietnam.digest(bundle)})
    scope = (
        "Official FRED public snapshot locally observed on the analysis date; "
        "replayed without a new download. Not an ALFRED revision archive or a "
        "guarantee of intraday availability before observed_at. Observation dates "
        "are measured periods, not release dates. US/euro series are not Vietnamese substitutes."
        if historical
        else SCOPE
    )
    text = (
        f"## FRED public: {bundle['title']} ({series})\n"
        f"- Units: {bundle['units']}\n- Adjustment: {bundle.get('units_and_adjustment', 'Not retained in older cache')}\n- Frequency: {bundle['frequency']}\n"
        f"- Window: {start} to {asof}\n- Observed: {bundle['observed_at']}\n"
        f"- Source updated: {bundle['source_last_updated']}\n- Scope: {scope}\n"
        f"- Source: {META_URL}{series}\n"
    )
    rows = bundle["observations"]
    if not rows:
        return text + "\nNo published observations returned in this window; missing is not zero."
    first, last = rows[0], rows[-1]
    text += f"\nLatest: {last['value']} ({last['date']}); level change from {first['date']}: {float(last['value']) - float(first['value']):+.6g} {bundle['units']}.\n"
    text += f"\nShowing last {min(len(rows), fred.MAX_ROWS)} of {len(rows)} observations.\n"
    return (
        text
        + "\n| Date | Value |\n| --- | --- |\n"
        + "\n".join(f"| {row['date']} | {row['value']} |" for row in rows[-fred.MAX_ROWS :])
        + "\n"
    )
