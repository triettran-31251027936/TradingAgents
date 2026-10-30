"""Official NSDP SDMX observations, with vintage, units and stale-data disclosure."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from desktop.vietnam import write_json

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ROOT = "https://nsdp.nso.gov.vn/GSO-chung/SDMXFiles/GSO/"
FILES = {"cpi": "CPIVNM.xml", "labor": "LMIVNM.xml"}
LOCK = threading.Lock()
MAX_BYTES = 8 * 1024 * 1024
# _PT rate definitions are checked against NSO's quarterly labour press release.
# The raw document incorrectly also assigns UNIT_MULT=3 to these rate series.
LABOR_REFERENCE = "https://www.nso.gov.vn/tin-tuc-thong-ke/2026/07/thong-cao-bao-chi-ve-tinh-hinh-lao-dong-viec-lam-quy-ii-va-6-thang-dau-nam-2026/"
SERIES = {
    "vn_cpi": ("cpi", "PCPI_IX", "Vietnam CPI index", "index"),
    "vn_labor_force": ("labor", "LLF_PE_NUM", "Vietnam labour force, age 15+", "persons"),
    "vn_employment": ("labor", "LE_PE_NUM", "Vietnam employed persons, age 15+", "persons"),
    "vn_unemployment": ("labor", "LUR_PT", "Vietnam working-age unemployment rate", "percent"),
    "vn_youth_unemployment": (
        "labor",
        "LUR15O_PT",
        "Vietnam youth unemployment rate, age 15-24",
        "percent",
    ),
    "vn_underemployment": (
        "labor",
        "LEU_PT",
        "Vietnam working-age underemployment rate",
        "percent",
    ),
}


def period_end(value, freq):
    pattern = (
        r"(\d{4})-(\d{2})" if freq == "M" else r"(\d{4})-Q([1-4])" if freq == "Q" else r"(\d{4})"
    )
    match = re.fullmatch(pattern, value)
    if not match:
        raise ValueError("Invalid SDMX observation period")
    year = int(match[1])
    month = int(match[2]) if freq == "M" else int(match[2]) * 3 if freq == "Q" else 12
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_xml(raw, asof, observed_at):
    if len(raw) > MAX_BYTES or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Invalid or oversized SDMX document")
    root = ET.fromstring(raw)
    if root.tag.rsplit("}", 1)[-1] != "StructureSpecificData":
        raise ValueError("Unsupported SDMX schema")
    prepared = next((e.text for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Prepared"), None)
    if not prepared:
        raise ValueError("Missing SDMX preparation timestamp")
    stamp = datetime.fromisoformat(prepared.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp > observed_at:
        raise ValueError("Future or unzoned SDMX preparation time")
    result = {}
    for series in root.iter():
        if series.tag.rsplit("}", 1)[-1] != "Series" or series.get("REF_AREA") != "VN":
            continue
        code = series.get("INDICATOR")
        freq = series.get("FREQ")
        if not code or freq not in ("M", "Q", "A"):
            continue
        if code in result:
            raise ValueError("Ambiguous SDMX indicator dimensions/base period")
        rows = []
        seen = set()
        for item in series:
            if item.tag.rsplit("}", 1)[-1] != "Obs":
                continue
            key = item.get("TIME_PERIOD", "")
            end = period_end(key, freq)
            if key in seen:
                raise ValueError("Duplicate SDMX observation period")
            seen.add(key)
            if end > asof:
                continue
            try:
                value = float(item.get("OBS_VALUE", ""))
            except (ValueError, TypeError):
                continue
            if not math.isfinite(value):
                continue
            rows.append(
                {
                    "period": key,
                    "period_end": end.isoformat(),
                    "raw_value": value,
                    "attributes": dict(item.attrib),
                }
            )
        rows.sort(key=lambda row: row["period_end"])
        result[code] = {
            "indicator": code,
            "frequency": freq,
            "dimensions": dict(series.attrib),
            "prepared_at": prepared,
            "observations": rows,
        }
    if not result:
        raise ValueError("No VN SDMX series")
    return result


def fetch(kind, cache_dir, asof, now, session):
    folder = Path(cache_dir) / "vn-macro" / kind
    folder.mkdir(parents=True, exist_ok=True)
    latest = folder / "latest.json"
    cached = (
        json.loads(latest.read_text()) if latest.is_file() and not latest.is_symlink() else None
    )
    if asof < now.date():
        candidates = []
        for path in folder.glob("*.snapshot.json"):
            if path.is_symlink():
                continue
            record = json.loads(path.read_text())
            if datetime.fromisoformat(record["first_observed_at"]).astimezone(TZ).date() <= asof:
                candidates.append(record)
        if not candidates:
            return {
                "status": "withheld",
                "reason": "No locally observed SDMX vintage available by analysis date",
                "url": ROOT + FILES[kind],
            }
        cached = max(candidates, key=lambda x: x["first_observed_at"])
    elif asof > now.date():
        return {"status": "withheld", "reason": "Future analysis date", "url": ROOT + FILES[kind]}
    elif (
        not cached
        or now.timestamp() - datetime.fromisoformat(cached["retrieved_at"]).timestamp() >= 21600
    ):
        try:
            time.sleep(0.35)
            response = session.get(ROOT + FILES[kind], timeout=(8, 20), stream=True)
            response.raise_for_status()
            chunks = []
            size = 0
            try:
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("SDMX response too large")
                    chunks.append(chunk)
            finally:
                response.close()
            raw = b"".join(chunks)
            parse_xml(raw, asof, now)
            digest = hashlib.sha256(raw).hexdigest()
            path = folder / (digest + ".xml")
            record_path = folder / (digest + ".snapshot.json")
            old = (
                json.loads(record_path.read_text())
                if record_path.is_file() and not record_path.is_symlink()
                else None
            )
            if path.is_symlink():
                raise ValueError("Invalid macro cache path")
            if not path.exists():
                path.write_bytes(raw)
                path.chmod(0o600)
            cached = {
                "status": "available",
                "url": ROOT + FILES[kind],
                "sha256": digest,
                "retrieved_at": now.isoformat(),
                "first_observed_at": old["first_observed_at"] if old else now.isoformat(),
            }
            write_json(record_path, cached)
            write_json(latest, cached)
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            if not cached:
                return {
                    "status": "unavailable",
                    "reason": type(exc).__name__,
                    "url": ROOT + FILES[kind],
                }
            cached = {**cached, "status": "cached_source_error", "error": type(exc).__name__}
    path = folder / (cached["sha256"] + ".xml")
    if path.is_symlink():
        raise ValueError("Invalid macro cache path")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != cached["sha256"]:
        raise ValueError("SDMX cache checksum mismatch")
    observed = datetime.fromisoformat(cached["first_observed_at"])
    return {**cached, "series": parse_xml(raw, asof, observed)}


def normalize(alias, series, source, asof):
    title, unit = SERIES[alias][2:]
    dim = series["dimensions"]
    raw_multiplier = dim.get("UNIT_MULT")
    if unit == "persons":
        if raw_multiplier not in ("0", "3", "6"):
            raise ValueError("Unknown population multiplier")
        factor = 10 ** int(raw_multiplier)
    else:
        factor = 1
    warnings = []
    if unit == "percent" and raw_multiplier != "0":
        warnings.append(
            "NSDP rate metadata UNIT_MULT="
            + str(raw_multiplier)
            + " conflicts with percentage definition; raw percent is retained without multiplying by 1000. Cross-check reference: "
            + LABOR_REFERENCE
        )
    if unit == "index" and raw_multiplier != "0":
        raise ValueError("Unknown CPI index scale")
    rows = []
    for row in series["observations"]:
        value = row["raw_value"] * factor
        if value < 0 or unit == "index" and value == 0 or unit == "percent" and value > 100:
            raise ValueError("Invalid non-negative/count/percent observation")
        rows.append({**row, "value": value})
    latest = rows[-1] if rows else None
    age = (asof - date.fromisoformat(latest["period_end"])).days if latest else None
    lagging = age is not None and age > (62 if series["frequency"] == "M" else 110)
    return {
        "status": ("stale" if lagging else "available") if latest else "missing",
        "title": title,
        "unit": unit,
        "source_url": source["url"],
        "source_connection_status": source["status"],
        "source_sha256": source["sha256"],
        "first_observed_at": source["first_observed_at"],
        "retrieved_at": source["retrieved_at"],
        "prepared_at": series["prepared_at"],
        "frequency": series["frequency"],
        "indicator": series["indicator"],
        "dimensions": dim,
        "unit_multiplier_applied": factor,
        "age_days": age,
        "freshness_policy": "lagging if month period end >62 days or quarter period end >110 days; not an official publication calendar",
        "latest": latest,
        "observations": rows,
        "warnings": warnings,
        "base_period": dim.get("BASE_PER"),
        "scope": "Official VN series; do not infer all-age unemployment from working-age rates or mix these populations",
    }


def load(asof, cache_dir, *, now=None, session=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = date.fromisoformat(asof)
    session = session or requests.Session()
    session.headers.update({"User-Agent": "TradingAgents-Research/0.8", "Accept": "*/*"})
    with LOCK:
        sources = {}
        for kind in FILES:
            try:
                sources[kind] = fetch(kind, cache_dir, target, now, session)
            except (OSError, ValueError, ET.ParseError) as exc:
                sources[kind] = {
                    "status": "unavailable",
                    "reason": type(exc).__name__,
                    "url": ROOT + FILES[kind],
                }
    output = {
        "asof": asof,
        "series": {},
        "sources": [],
        "warnings": [],
        "scope": "NSDP file observations, not necessarily the newest official press release; no interpolation or historical publication-date assumptions",
    }
    for kind, source in sources.items():
        output["sources"].append({k: v for k, v in source.items() if k != "series"})
        if source.get("status") != "available":
            output["warnings"].append(kind + ": " + source.get("reason", source["status"]))
    for alias, (kind, code, *_) in SERIES.items():
        source = sources[kind]
        series = source.get("series", {}).get(code)
        if not series:
            output["series"][alias] = {
                "status": source.get("status", "missing"),
                "reason": source.get("reason", "Indicator absent"),
                "latest": None,
            }
            continue
        try:
            normalized = normalize(alias, series, source, target)
        except ValueError as exc:
            normalized = {"status": "unavailable", "reason": str(exc), "latest": None}
        output["series"][alias] = normalized
        if normalized.get("status") == "stale":
            output["warnings"].append(
                alias + ": stale source period " + normalized["latest"]["period"]
            )
    cpi = output["series"]["vn_cpi"]
    for alias, offset in [("vn_cpi_yoy", 12), ("vn_cpi_mom", 1)]:
        by_period = {r["period"]: r for r in cpi.get("observations", [])}
        rows = []
        for row in by_period.values():
            year, month = map(int, row["period"].split("-"))
            n = year * 12 + month - 1 - offset
            prior = by_period.get(f"{n // 12:04d}-{n % 12 + 1:02d}")
            if prior and prior["value"] > 0:
                rows.append(
                    {
                        **row,
                        "value": (row["value"] / prior["value"] - 1) * 100,
                        "prior_period": prior["period"],
                    }
                )
        output["series"][alias] = {
            **cpi,
            "title": "Vietnam CPI " + ("YoY" if offset == 12 else "MoM"),
            "unit": "percent",
            "formula": f"(same-base CPI / CPI {offset} months earlier - 1) * 100",
            "observations": rows,
            "latest": rows[-1] if rows else None,
        }
    output["raw_series"] = {
        code: {**series, "source_url": sources[kind]["url"]}
        for kind in FILES
        for code, series in sources[kind].get("series", {}).items()
    }
    return output


def summary(bundle):
    return {k: v for k, v in bundle.items() if k not in ("series", "raw_series")} | {
        "series": {
            alias: {k: v for k, v in entry.items() if k != "observations"}
            for alias, entry in bundle.get("series", {}).items()
        }
    }


def tool(bundle, indicator, asof, look_back_days=None):
    if asof != bundle.get("asof"):
        raise ValueError("Macro request date differs from canonical snapshot")
    days = 365 if look_back_days is None else look_back_days
    if type(days) is not int or not 1 <= days <= 36500:
        raise ValueError("Invalid macro lookback")
    start = (date.fromisoformat(asof) - timedelta(days=days)).isoformat()
    if indicator.startswith("vn_release_"):
        release = bundle.get("official_releases", {})
        metric = release.get("metrics", {}).get(indicator)
        return json.dumps(
            {
                "indicator": indicator,
                "metric": metric,
                "scope": release.get("scope"),
                "sources": release.get("releases", []),
                "status": "available"
                if metric and metric.get("value") is not None
                else "unavailable",
            },
            ensure_ascii=False,
        )
    if indicator.startswith("vn_nsdp:"):
        series = bundle.get("raw_series", {}).get(indicator.split(":", 1)[1])
        if not series:
            return json.dumps({"status": "unsupported", "reason": "Unknown VN NSDP indicator"})
        result = {
            **series,
            "scope": "Raw source values and dimensions; not normalized. Check unit multiplier and definition before using.",
        }
    else:
        series = bundle.get("series", {}).get(indicator)
        if not series:
            return json.dumps(
                {
                    "status": "unsupported",
                    "reason": "Unsupported VN alias",
                    "available": list(bundle.get("series", {})),
                }
            )
        result = {**series}
    result["observations"] = [
        r for r in series.get("observations", []) if r["period_end"] >= start
    ][-40:]
    result["window_start"] = start
    result["coverage"] = (
        "Source file may lag newer press releases; latest is a file observation, not a claim of the latest national release. No observations in window means missing coverage."
    )
    return json.dumps(result, ensure_ascii=False, allow_nan=False)


def metrics(bundle):
    result = {}
    for alias, entry in bundle.get("series", {}).items():
        latest = entry.get("latest")
        unit = entry.get("unit", "unknown")
        value = latest["value"] if latest else None
        formula = entry.get(
            "formula",
            "Official NSDP SDMX "
            + str(entry.get("indicator", alias))
            + "; unit multiplier applied "
            + str(entry.get("unit_multiplier_applied", "unknown")),
        )
        if unit == "percent":
            value = value / 100 if value is not None else None
            unit = "fraction"
            formula += "; raw percent divided by 100"
        formula += (
            "; source status "
            + entry["status"]
            + "; observation is not necessarily the latest official release"
        )
        result[alias] = {
            "value": value,
            "unit": unit,
            "formula": formula,
            "source": entry.get("source_url", "NSDP Vietnam; unavailable"),
            "asof": latest["period_end"] if latest else bundle.get("asof"),
            "status": entry["status"] if latest else "missing",
        }
    return result
