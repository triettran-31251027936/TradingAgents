"""Official Vietcombank reference bid/ask quotes; never SBV central FX rates."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from desktop.vietnam import write_json

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
URL = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
SCOPE = "Vietcombank posted retail reference quotes, VND per unit of foreign currency. Not SBV central rates, interbank mid-prices or guaranteed executable quotes."
ALIASES = {
    "vn_fx_usd_cash_buy": "cash_buy",
    "vn_fx_usd_transfer_buy": "transfer_buy",
    "vn_fx_usd_sell": "sell",
}
LOCK = threading.Lock()


def price(value):
    if value in (None, "", "-"):
        return None
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", value
    ):
        raise ValueError("Invalid VCB quote format")
    result = float(value.replace(",", ""))
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Non-positive VCB quote")
    return result


def parse(raw, now):
    if len(raw) > 1048576 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Invalid FX XML")
    root = ET.fromstring(raw)
    if root.tag != "ExrateList":
        raise ValueError("Unexpected FX schema")
    date_text = root.findtext("DateTime")
    if not date_text:
        raise ValueError("Missing bank quote timestamp")
    stamp = datetime.strptime(date_text.strip(), "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=TZ)
    if stamp > now:
        raise ValueError("Future bank quote timestamp")
    result = {}
    for row in root.findall("Exrate"):
        code = row.get("CurrencyCode", "")
        if not re.fullmatch(r"[A-Z]{3}", code) or code in result:
            raise ValueError("Invalid or duplicate FX currency")
        values = {
            name: price(row.get(key))
            for name, key in [("cash_buy", "Buy"), ("transfer_buy", "Transfer"), ("sell", "Sell")]
        }
        if values["sell"] is not None and any(
            value > values["sell"]
            for key, value in values.items()
            if key != "sell" and value is not None
        ):
            raise ValueError("Bank buy quote exceeds sell quote")
        result[code] = {"name": row.get("CurrencyName", "").strip(), **values}
    if not result:
        raise ValueError("No bank quotes")
    return {"quote_time": stamp.isoformat(), "currencies": result}


def load(asof, cache_dir, *, now=None, session=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    target = datetime.strptime(asof, "%Y-%m-%d").date()
    empty = {"status": "unavailable", "currencies": {}, "source_url": URL, "scope": SCOPE}
    if target > now.date():
        return {**empty, "status": "withheld", "reason": "Future date"}
    folder = Path(cache_dir) / "vn-macro" / "bank-fx"
    folder.mkdir(parents=True, exist_ok=True)
    with LOCK:
        path = folder / "latest.json"
        cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
        if target < now.date():
            versions = []
            for item in folder.glob("*.snapshot.json"):
                if item.is_symlink():
                    continue
                record = json.loads(item.read_text())
                if (
                    datetime.fromisoformat(record["first_observed_at"]).astimezone(TZ).date()
                    <= target
                ):
                    versions.append(record)
            if not versions:
                return {
                    **empty,
                    "status": "withheld",
                    "reason": "No observed bank quote vintage by requested date",
                }
            cached = max(versions, key=lambda x: x["first_observed_at"])
        elif (
            not cached
            or now.timestamp() - datetime.fromisoformat(cached["retrieved_at"]).timestamp() >= 1800
        ):
            session = session or requests.Session()
            session.headers.update({"User-Agent": "TradingAgents-Research/0.8", "Accept": "*/*"})
            try:
                response = session.get(URL, timeout=(8, 20))
                response.raise_for_status()
                raw = response.content
                data = parse(raw, now)
                digest = hashlib.sha256(raw).hexdigest()
                snapshot = folder / (digest + ".snapshot.json")
                old = (
                    json.loads(snapshot.read_text())
                    if snapshot.is_file() and not snapshot.is_symlink()
                    else None
                )
                raw_path = folder / (digest + ".xml")
                if raw_path.is_symlink():
                    raise ValueError("Invalid FX cache path")
                if not raw_path.exists():
                    raw_path.write_bytes(raw)
                    raw_path.chmod(0o600)
                cached = {
                    **data,
                    "sha256": digest,
                    "retrieved_at": now.isoformat(),
                    "first_observed_at": old["first_observed_at"] if old else now.isoformat(),
                }
                write_json(snapshot, cached)
                write_json(path, cached)
            except (requests.RequestException, ValueError, ET.ParseError) as exc:
                if not cached:
                    return {**empty, "reason": type(exc).__name__}
                cached = {**cached, "connection_error": type(exc).__name__}
        raw_path = folder / (cached["sha256"] + ".xml")
        if raw_path.is_symlink():
            raise ValueError("Invalid FX cache path")
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != cached["sha256"]:
            raise ValueError("FX cache checksum mismatch")
        data = parse(raw, now)
        age = max(0, int(now.timestamp() - datetime.fromisoformat(data["quote_time"]).timestamp()))
        status = (
            "cached_source_error"
            if cached.get("connection_error")
            else "stale"
            if age > 86400
            else "available"
        )
        return {
            **cached,
            **data,
            "status": status,
            "age_seconds": age,
            "source_url": URL,
            "scope": SCOPE,
        }


def metrics(bundle, asof):
    row = bundle.get("currencies", {}).get("USD", {})
    return {
        alias: {
            "value": row.get(key),
            "unit": "VND/USD",
            "asof": bundle.get("quote_time", asof),
            "source": URL,
            "formula": "Vietcombank USD "
            + key
            + " posted reference quote; not SBV central rate or an executable order price",
            "status": bundle["status"] if row.get(key) is not None else "missing",
        }
        for alias, key in ALIASES.items()
    }


def tool(bundle, indicator):
    if indicator not in ALIASES and not indicator.startswith("vn_fx:"):
        return json.dumps({"status": "unsupported", "scope": SCOPE})
    code = indicator.split(":", 1)[1] if indicator.startswith("vn_fx:") else "USD"
    field = ALIASES.get(indicator)
    currency = bundle.get("currencies", {}).get(code)
    return json.dumps(
        {
            "scope": SCOPE,
            "status": bundle.get("status", "unavailable") if currency else "unavailable",
            "currency": code,
            "quotes": currency,
            "requested_field": field,
            "value": currency.get(field) if currency and field else None,
            "quote_time": bundle.get("quote_time"),
            "retrieved_at": bundle.get("retrieved_at"),
            "source_url": URL,
        },
        ensure_ascii=False,
    )
