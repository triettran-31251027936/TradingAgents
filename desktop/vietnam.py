"""Canonical VN data and deterministic facts shared by every original graph role.

Independent HTTP adapter to public Vietcap endpoints. No vnstock code or field
mapping is distributed: statement titles come from the provider's metrics API.
No credentials, impersonation, rate-limit bypass or fallback to US fundamentals.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, time as day_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from stockstats import wrap

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CHART = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
FINANCE = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company"
VERSION = 10
LOCK = threading.Lock()


def symbol(value):
    value = str(value).strip().upper().removesuffix(".VN")
    if not re.fullmatch(r"[A-Z][A-Z0-9]{2,8}", value):
        raise ValueError("Mã chứng khoán Việt Nam không hợp lệ.")
    return value


def is_vietnam(ticker):
    return str(ticker).upper().endswith(".VN")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    if path.is_symlink() or tmp.is_symlink():
        raise ValueError("Không ghi dữ liệu qua file liên kết.")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
    tmp.chmod(0o600)
    tmp.replace(path)


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _safe(value):
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(x) for x in value]
    if isinstance(value, (np.integer, np.floating)):
        return _number(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _request(session, url, payload=None):
    # Requests are serial and cached. A denial/rate limit is surfaced, not bypassed.
    time.sleep(0.35)
    response = (
        session.post(url, json=payload, timeout=25) if payload else session.get(url, timeout=25)
    )
    response.raise_for_status()
    result = response.json()
    if isinstance(result, dict) and result.get("code") not in (None, 0):
        raise ValueError("Vietcap trả về lỗi dữ liệu.")
    return result


def normalize_prices(raw, asof, now=None):
    now = now or datetime.now(TZ)
    items = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(items, dict) or not all(k in items for k in ("t", "o", "h", "l", "c", "v")):
        raise ValueError("Nguồn không trả về OHLCV đúng schema.")
    sizes = {len(items[k]) for k in ("t", "o", "h", "l", "c", "v")}
    if len(sizes) != 1:
        raise ValueError("Các cột OHLCV khác độ dài.")
    rows = []
    for t, o, h, low, c, v in zip(*(items[k] for k in ("t", "o", "h", "l", "c", "v")), strict=True):
        stamp = pd.to_datetime(int(t), unit="s", utc=True).tz_convert(TZ).strftime("%Y-%m-%d")
        if stamp > asof or (stamp == now.date().isoformat() and now.time() < day_time(15, 0)):
            continue  # a live partial daily candle is not a completed session
        nums = [_number(x) for x in (o, h, low, c, v)]
        if any(x is None for x in nums) or min(nums[:4]) <= 0 or nums[4] < 0:
            raise ValueError("OHLCV chứa giá/khối lượng không hợp lệ; không nội suy.")
        if not (nums[2] <= min(nums[0], nums[3]) <= max(nums[0], nums[3]) <= nums[1]):
            raise ValueError("OHLCV vi phạm quan hệ high/low.")
        rows.append(
            dict(
                zip(("date", "open", "high", "low", "close", "volume"), [stamp, *nums], strict=True)
            )
        )
    rows.sort(key=lambda r: r["date"])
    if len({r["date"] for r in rows}) != len(rows):
        raise ValueError("Nguồn trả về hai nến cùng ngày; cần đối soát.")
    if not rows:
        raise ValueError("Không có nến hoàn tất vào/trước ngày phân tích.")
    return rows


def _published(row, asof):
    published = row.get("publicDate")
    if not isinstance(published, str):
        return False
    try:
        return date.fromisoformat(published[:10]) <= date.fromisoformat(asof)
    except ValueError:
        return False


def normalize_news(raw, start, end, url):
    content = (raw.get("data") or {}).get("content", [])
    result = []
    seen = set()
    for row in content:
        day = str(row.get("publicDate") or "")[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        key = row.get("newsId") or row.get("id")
        if not start <= day <= end or key in seen or not row.get("newsTitle"):
            continue
        seen.add(key)
        link = row.get("newsSourceLink")
        result.append(
            {
                "id": key,
                "published": row["publicDate"],
                "headline": row["newsTitle"],
                "summary": row.get("newsShortContent"),
                "url": link
                if isinstance(link, str) and link.startswith(("http://", "https://"))
                else url,
                "coverage": "headline/short summary only; full article not retrieved",
            }
        )
    return result


def statements(raw, metadata, asof):
    result = {}
    for kind, section in [
        ("income", "INCOME_STATEMENT"),
        ("balance", "BALANCE_SHEET"),
        ("cash", "CASH_FLOW"),
    ]:
        mapping = {
            r["field"]: r.get("titleEn", r["field"])
            for r in metadata.get(section, [])
            if r.get("field")
        }
        data = raw.get(kind, {}).get("data") or {}
        result[kind] = {
            "fields": mapping,
            "field_metadata": {r["field"]: r for r in metadata.get(section, []) if r.get("field")},
        }
        for period in ("quarters", "years"):
            rows = [r for r in data.get(period, []) if _published(r, asof)]
            rows.sort(key=lambda r: (int(r["yearReport"]), int(r.get("lengthReport", 5))))
            result[kind][period] = rows
    return result


def field(book, kind, *titles):
    for key, label in book[kind]["fields"].items():
        if label.lower().strip() in {t.lower() for t in titles}:
            return key
    return None


def compute(
    rows,
    book,
    benchmark=None,
    *,
    valuation_raw=None,
    asof=None,
    observed_at=None,
    valuation_proof=None,
    company_profile=None,
):
    df = pd.DataFrame(rows).set_index("date")
    close = df["close"]
    ret = close.pct_change(fill_method=None).dropna()
    stock = wrap(df.copy())
    metrics = {}
    missing = []

    def add(name, value, unit, formula, source="Vietcap OHLCV", asof=None):
        value = _number(value)
        metrics[name] = {
            "value": value,
            "unit": unit,
            "formula": formula,
            "source": source,
            "asof": asof or rows[-1]["date"],
            "status": "available" if value is not None else "missing",
        }
        if value is None:
            missing.append(name)

    add("close", close.iloc[-1], "VND/share", "Last completed close")
    add("volume", df["volume"].iloc[-1], "shares", "Last completed volume")
    add(
        "adv20",
        df["volume"].tail(20).mean() if len(df) >= 20 else None,
        "shares/day",
        "mean(volume,last 20 sessions)",
    )
    add(
        "turnover20",
        (df["close"] * df["volume"]).tail(20).mean() if len(df) >= 20 else None,
        "VND/day",
        "mean(close × volume,20); daily-close approximation, not transaction turnover",
    )
    for n in (20, 60, 252):
        add(
            f"return_{n}d",
            close.iloc[-1] / close.iloc[-n - 1] - 1 if len(df) > n else None,
            "fraction",
            f"close / close[{n} sessions ago] - 1",
        )
    add(
        "volatility20_annual",
        ret.tail(20).std(ddof=1) * math.sqrt(252) if len(ret) >= 20 else None,
        "fraction/year",
        "sample std(daily returns,20) × sqrt(252)",
    )
    window = close.tail(252)
    add(
        "max_drawdown252",
        (window / window.cummax() - 1).min() if len(window) >= 20 else None,
        "fraction",
        "min(close / running max(close) - 1), up to252 sessions",
    )
    threshold = ret.tail(252).quantile(0.05) if len(ret) >= 60 else None
    add(
        "historical_var95_1d",
        max(0, -threshold) if threshold is not None else None,
        "fraction/day",
        "max(0,-5th percentile daily returns), up to252 sessions; historical, not forecast",
    )
    tail = (
        ret.tail(252)[ret.tail(252) <= threshold]
        if threshold is not None
        else pd.Series(dtype=float)
    )
    add(
        "historical_cvar95_1d",
        max(0, -tail.mean()) if len(tail) else None,
        "fraction/day",
        "mean losses in worst historical5% days",
    )
    for name, min_rows in [
        ("close_10_ema", 10),
        ("close_50_sma", 50),
        ("close_200_sma", 200),
        ("rsi", 15),
        ("atr", 15),
        ("boll", 20),
        ("boll_ub", 20),
        ("boll_lb", 20),
        ("macd", 35),
        ("macds", 35),
        ("macdh", 35),
    ]:
        try:
            value = stock[name].iloc[-1] if len(df) >= min_rows else None
        except (KeyError, ValueError):
            value = None
        add(
            name,
            value,
            "index" if name == "rsi" else "VND/share",
            "stockstats " + name + "; no filled OHLCV gaps",
        )
    beta = None
    if benchmark:
        idx = pd.DataFrame(benchmark).set_index("date")["close"].pct_change(fill_method=None)
        aligned = pd.concat([ret.rename("stock"), idx.rename("index")], axis=1).dropna().tail(60)
        if len(aligned) >= 20 and aligned["index"].var() > 0:
            beta = aligned["stock"].cov(aligned["index"]) / aligned["index"].var()
    add(
        "beta60_vnindex",
        beta,
        "ratio",
        "cov(stock returns,VNINDEX)/var(VNINDEX), last60 common sessions; min20",
        "Vietcap stock + VNINDEX",
    )
    for kind, title, aliases, unit in [
        ("income", "revenue", ("Net sales",), "VND"),
        ("income", "gross_profit", ("Gross Profit",), "VND"),
        ("income", "operating_profit", ("Operating profit/(loss)",), "VND"),
        ("income", "net_profit_parent", ("Attributable to parent company",), "VND"),
        ("balance", "total_assets", ("Total Assets",), "VND"),
        ("balance", "equity", ("Owner's Equity",), "VND"),
        ("balance", "current_assets", ("CURRENT ASSETS",), "VND"),
        ("balance", "current_liabilities", ("Current liabilities",), "VND"),
        ("balance", "short_term_borrowings", ("Short-term borrowings",), "VND"),
        ("balance", "long_term_borrowings", ("Long-term borrowings",), "VND"),
        (
            "cash",
            "operating_cashflow",
            (
                "Net cash inflows/(outflows) from operating activities",
                "Net cash from operating activities",
            ),
            "VND",
        ),
        (
            "cash",
            "capex_cash_outflow",
            ("Purchases of fixed assets and other long term assets",),
            "VND",
        ),
    ]:
        key = field(book, kind, *aliases)
        latest = book[kind]["quarters"][-1] if book[kind]["quarters"] else {}
        period = f"{latest.get('yearReport', '?')}-Q{latest.get('lengthReport', '?')} public:{str(latest.get('publicDate', ''))[:10]}"
        add(
            title,
            latest.get(key) if key else None,
            unit,
            f"Provider field {key}: {aliases[0]}; latest published quarter",
            "Vietcap financial statements",
            period,
        )

    def val(name):
        return metrics[name]["value"]

    def divide(a, b):
        return a / b if a is not None and b not in (None, 0) else None

    add(
        "current_ratio",
        divide(val("current_assets"), val("current_liabilities")),
        "ratio",
        "current_assets/current_liabilities",
        "Vietcap balance sheet",
        metrics["current_assets"]["asof"],
    )
    debt = (
        val("short_term_borrowings") + val("long_term_borrowings")
        if all(val(k) is not None for k in ("short_term_borrowings", "long_term_borrowings"))
        else None
    )
    add(
        "borrowings_to_equity",
        divide(debt, val("equity")),
        "ratio",
        "(short-term + long-term borrowings)/equity",
        "Vietcap balance sheet",
        metrics["equity"]["asof"],
    )
    add(
        "gross_margin",
        divide(val("gross_profit"), val("revenue")),
        "fraction",
        "gross profit/net sales; same published quarter",
        "Vietcap income statement",
        metrics["revenue"]["asof"],
    )
    income = book["income"]["quarters"]
    key = field(book, "income", "Net sales")
    growth = None
    if income and key:
        last = income[-1]
        previous = next(
            (
                r
                for r in income
                if int(r["yearReport"]) == int(last["yearReport"]) - 1
                and int(r["lengthReport"]) == int(last["lengthReport"])
            ),
            None,
        )
        if previous and previous.get(key):
            growth = last[key] / previous[key] - 1
    add(
        "revenue_yoy",
        growth,
        "fraction",
        "same quarter revenue / previous-year quarter revenue - 1",
        "Vietcap income statement",
        metrics["revenue"]["asof"],
    )
    from desktop.financial_metrics import extend

    extend(book, add, field)
    from desktop.valuation import extend as extend_valuation

    proof = extend_valuation(
        book,
        rows,
        valuation_raw or {},
        asof or rows[-1]["date"],
        observed_at or datetime.now(TZ),
        add,
        field,
        company_profile=company_profile,
    )
    if valuation_proof is not None:
        valuation_proof.update(proof)
    return _safe(metrics), missing


def load(ticker, asof, cache_dir, *, refresh=False, session=None, prices_only=False):
    ticker = symbol(ticker)
    date.fromisoformat(asof)
    folder = Path(cache_dir) / "vietnam"
    path = folder / (f"{ticker}_{asof}_v{VERSION}" + ("_prices.json" if prices_only else ".json"))
    with LOCK:
        if (
            path.exists()
            and not path.is_symlink()
            and not refresh
            and time.time() - path.stat().st_mtime < 3600
        ):
            cached = json.loads(path.read_text())
            now = datetime.now(TZ)
            crossed_close = (
                asof == now.date().isoformat()
                and now.time() >= day_time(15, 0)
                and datetime.fromtimestamp(path.stat().st_mtime, TZ).time() < day_time(15, 0)
            )
            if not crossed_close:
                return cached
        session = session or requests.Session()
        session.headers.update(
            {
                "User-Agent": "TradingAgents-Research/0.8",
                "Accept": "application/json",
                "Referer": "https://trading.vietcap.com.vn/",
            }
        )
        end = datetime.combine(date.fromisoformat(asof), day_time(23, 59, 59), TZ)
        cutoff = min(end, datetime.now(TZ))
        raw = {}
        warnings = []
        sources = []

        def fetch(name, url, payload=None, required=False):
            try:
                value = _request(session, url, payload)
                raw[name] = value
                sources.append(
                    {
                        "name": name,
                        "url": url,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                        "sha256": digest(value),
                    }
                )
                return value
            except (requests.RequestException, ValueError, KeyError) as e:
                if required:
                    raise RuntimeError(
                        f"Không lấy được dữ liệu giá Việt Nam ({type(e).__name__})."
                    ) from None
                warnings.append(f"{name}: {type(e).__name__}; missing, not inferred")
                return {}

        prices = normalize_prices(
            fetch(
                "prices",
                CHART,
                {
                    "timeFrame": "ONE_DAY",
                    "symbols": [ticker],
                    "to": int(cutoff.timestamp()),
                    "countBack": 600,
                },
                True,
            ),
            asof,
        )
        news_start = (date.fromisoformat(asof) - timedelta(days=7)).isoformat()
        news_url = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker={ticker}&fromDate={news_start}&toDate={asof}&languageId=1&page=0&size=30"
        news = (
            []
            if prices_only
            else normalize_news(fetch("news", news_url), news_start, asof, news_url)
        )
        benchmark_raw = (
            {}
            if prices_only
            else fetch(
                "benchmark",
                CHART,
                {
                    "timeFrame": "ONE_DAY",
                    "symbols": ["VNINDEX"],
                    "to": int(cutoff.timestamp()),
                    "countBack": 600,
                },
            )
        )
        try:
            benchmark = normalize_prices(benchmark_raw, asof) if benchmark_raw else []
        except ValueError:
            benchmark = []
            warnings.append("VNINDEX unavailable")
        metadata = (
            {}
            if prices_only
            else fetch("fields", f"{FINANCE}/{ticker}/financial-statement/metrics").get("data")
            or {}
        )
        if not prices_only:
            for name, section in [
                ("income", "INCOME_STATEMENT"),
                ("balance", "BALANCE_SHEET"),
                ("cash", "CASH_FLOW"),
            ]:
                fetch(name, f"{FINANCE}/{ticker}/financial-statement?section={section}")
        book = statements(raw, metadata, asof)
        valuation_raw = (
            {} if prices_only else fetch("valuation", f"{FINANCE}/{ticker}/statistics-financial")
        )
        details = (
            {} if prices_only else fetch("company_details", f"{FINANCE}/details?ticker={ticker}")
        )
        valuation_observed_at = datetime.now(TZ).isoformat()
        from desktop.company_profile import normalize as normalize_company

        company_profile = normalize_company(details, ticker, asof, valuation_observed_at)
        valuation_proof = {}
        metrics, missing = compute(
            prices,
            book,
            benchmark,
            valuation_raw=valuation_raw,
            asof=asof,
            observed_at=valuation_observed_at,
            valuation_proof=valuation_proof,
            company_profile=company_profile,
        )
        from desktop.bank_metrics import extend as extend_bank

        def bank_add(name, value, unit, formula, source, stamp):
            metrics[name] = {
                "value": value,
                "unit": unit,
                "formula": formula,
                "source": source,
                "asof": stamp,
                "status": "provider_observed"
                if value is not None and name.startswith("bank_provider_")
                else "available"
                if value is not None
                else "missing",
            }

        bank_proof = extend_bank(
            book,
            bank_add,
            valuation_raw=valuation_raw,
            asof=asof,
            observed_at=valuation_observed_at,
        )
        from desktop.applicability import apply_bank, missing as missing_metrics

        apply_bank(metrics, bank_proof)
        missing = missing_metrics(metrics)
        from desktop.financial_metrics import reconcile

        reconciliation = {
            kind: {
                key: reconcile(book[kind], key)
                for key in book[kind]["fields"]
                if any(
                    term in book[kind]["fields"][key].lower()
                    for term in ("net sales", "operating activities", "purchases of fixed")
                )
            }
            for kind in ("income", "cash")
        }
        domestic_macro = {}
        if not prices_only:
            from desktop import macro_vietnam

            domestic_macro = macro_vietnam.load(asof, cache_dir)
            metrics.update(macro_vietnam.metrics(domestic_macro))
            from desktop.macro_releases import load as load_releases

            releases = load_releases(asof, cache_dir)
            domestic_macro["official_releases"] = releases
            metrics.update(releases["metrics"])
            domestic_macro["warnings"].extend(releases["warnings"])
            from desktop import fx_vietnam

            try:
                bank_fx = fx_vietnam.load(asof, cache_dir)
            except (OSError, ValueError) as exc:
                bank_fx = {
                    "status": "unavailable",
                    "currencies": {},
                    "source_url": fx_vietnam.URL,
                    "reason": type(exc).__name__,
                }
            domestic_macro["bank_fx"] = bank_fx
            metrics.update(fx_vietnam.metrics(bank_fx, asof))
            if bank_fx["status"] != "available":
                domestic_macro["warnings"].append("Vietcombank FX: " + bank_fx["status"])
            sources.append(
                {
                    "name": "Vietcombank reference FX",
                    "url": fx_vietnam.URL,
                    "retrieved_at": bank_fx.get("retrieved_at", "unavailable"),
                    "sha256": bank_fx.get("sha256"),
                    "status": bank_fx["status"],
                }
            )
            missing = missing_metrics(metrics)
            warnings.extend(domestic_macro.get("warnings", []))
            for source in domestic_macro.get("sources", []) + releases.get("sources", []):
                sources.append(
                    {
                        "name": source.get("name", "Vietnam NSDP macro"),
                        "url": source["url"],
                        "retrieved_at": source.get("retrieved_at", "unavailable"),
                        "sha256": source.get("sha256"),
                        "status": source["status"],
                    }
                )
        insider_dealings = {}
        if not prices_only:
            from desktop import insider_vietnam

            try:
                insider_dealings = insider_vietnam.load(ticker, asof, cache_dir)
            except (OSError, ValueError) as exc:
                insider_dealings = {
                    "ticker": ticker + ".VN",
                    "asof": asof,
                    "source_url": insider_vietnam.URL,
                    "source_page": f"https://cafef.vn/du-lieu/Lich-su-giao-dich-{ticker.lower()}-6.chn",
                    "status": "unavailable",
                    "records": [],
                    "warnings": [type(exc).__name__],
                }
            metrics.update(insider_vietnam.metrics(insider_dealings))
            missing = missing_metrics(metrics)
            warnings.extend(insider_dealings.get("warnings", []))
            sources.append(
                {
                    "name": "CafeF shareholder dealings",
                    "url": insider_vietnam.URL,
                    "retrieved_at": insider_dealings.get("retrieved_at", "unavailable"),
                    "sha256": insider_dealings.get("source_sha256"),
                    "status": insider_dealings["status"],
                }
            )
        domestic_social = {}
        if not prices_only:
            from desktop import social_vietnam

            try:
                domestic_social = social_vietnam.load(ticker, asof, cache_dir)
            except (OSError, ValueError) as exc:
                domestic_social = {
                    "ticker": ticker + ".VN",
                    "asof": asof,
                    "status": "unavailable",
                    "records": [],
                    "context_records": [],
                    "warnings": [type(exc).__name__],
                    "source_url": f"https://f247.com/tag/{ticker}.rss",
                    "scope": social_vietnam.SCOPE,
                }
            warnings.extend(domestic_social.get("warnings", []))
            sources.append(
                {
                    "name": "F247 public community RSS sample",
                    "url": domestic_social["source_url"],
                    "retrieved_at": domestic_social.get("retrieved_at", "unavailable"),
                    "sha256": domestic_social.get("source_sha256"),
                    "status": domestic_social["status"],
                }
            )
        universe_path = folder / "symbols.json"
        universe = (
            json.loads(universe_path.read_text())
            if universe_path.is_file()
            and not universe_path.is_symlink()
            and time.time() - universe_path.stat().st_mtime < 86400
            else fetch("symbols", "https://trading.vietcap.com.vn/api/price/symbols/getAll")
        )
        if universe:
            write_json(universe_path, universe)
        listing = universe.get("data", []) if isinstance(universe, dict) else universe
        instrument = (
            next((r for r in listing if r.get("symbol") == ticker), {})
            if isinstance(listing, list)
            else {}
        )
        exchange = instrument.get("board") or instrument.get("exchange")
        exchange = {"HSX": "HOSE"}.get(exchange, exchange)
        bundle = {
            "exchange": exchange,
            "company_name": instrument.get("enOrganName") or instrument.get("organName") or ticker,
            "version": VERSION,
            "ticker": ticker + ".VN",
            "analysis_date": asof,
            "currency": "VND",
            "provider": "Vietcap public market data",
            "prices": prices,
            "benchmark": benchmark,
            "domestic_macro": domestic_macro,
            "insider_dealings": insider_dealings,
            "domestic_social": domestic_social,
            "statements": book,
            "metrics": metrics,
            "financial_reconciliation": reconciliation,
            "company_profile": company_profile,
            "valuation_observed_at": valuation_observed_at,
            "valuation_reconciliation": valuation_proof,
            "bank_reconciliation": bank_proof,
            "missing": missing,
            "warnings": warnings,
            "news": news,
            "sources": sources,
            "basis": [
                "Prices as returned by source; historical corporate-action adjustments may apply.",
                "Published-date filtering is date-level. Restated accounts are not a point-in-time archive.",
                "Statement values are raw VND except per-share/ratio/share-count fields. No conversion from billions.",
                "TTM flows require contiguous quarters and empirical quarter/FY reconciliation within0.5%; methodology can change. See financial_reconciliation. Raw CFO is not automatically TTM FCF.",
            ],
        }
        bundle = _safe(bundle)
        bundle["fingerprint"] = digest({k: v for k, v in bundle.items() if k != "sources"})
        write_json(path, bundle)
        write_json(folder / (path.stem + "_raw.json"), raw)
        return bundle


def capsule(bundle):
    from desktop.macro_vietnam import summary as macro_summary

    result = json.dumps(
        {
            k: bundle[k]
            for k in [
                "ticker",
                "company_name",
                "company_profile",
                "exchange",
                "analysis_date",
                "currency",
                "provider",
                "bank_reconciliation",
                "valuation_reconciliation",
                "metrics",
                "missing",
                "warnings",
                "basis",
                "fingerprint",
            ]
            if k in bundle
        },
        ensure_ascii=False,
        allow_nan=False,
    )
    decoded = json.loads(result)
    if bundle.get("domestic_macro"):
        decoded["domestic_macro"] = macro_summary(bundle["domestic_macro"])
    if bundle.get("insider_dealings"):
        from desktop.insider_vietnam import summary as insider_summary

        decoded["insider_dealings"] = insider_summary(bundle["insider_dealings"])
    if bundle.get("domestic_social"):
        from desktop.social_vietnam import summary as social_summary

        decoded["domestic_social"] = social_summary(bundle["domestic_social"])
    return json.dumps(decoded, ensure_ascii=False, allow_nan=False)


@contextmanager
def vendor_scope(bundle):
    """Keep upstream tools/nodes, replace only their VN data transport in this job."""
    if bundle is None:
        yield
        return
    from tradingagents.agents import tools
    from tradingagents.dataflows import router

    original_snapshot = tools.build_verified_market_snapshot
    prior = {}

    def guard(ticker):
        if symbol(ticker) != symbol(bundle["ticker"]):
            raise ValueError("Snapshot thuộc mã khác.")

    def prices(ticker, start, end):
        guard(ticker)
        frame = pd.DataFrame([r for r in bundle["prices"] if start <= r["date"] <= end])
        return (
            "Source: Vietcap; prices VND/share, volume shares; completed candles only.\n"
            + frame.to_csv(index=False)
        )

    def indicator(ticker, name, asof, look_back_days=30):
        guard(ticker)
        frame = pd.DataFrame([r for r in bundle["prices"] if r["date"] <= asof]).set_index("date")
        if name == "vwma":
            values = (frame.close * frame.volume).rolling(20).sum() / frame.volume.rolling(20).sum()
        else:
            values = wrap(frame.copy())[name]
        return (
            "Source: Vietcap OHLCV; Python stockstats; VND except RSI.\n"
            + values.tail(max(1, min(look_back_days, 252))).to_csv()
        )

    def statement(kind):
        def read(ticker, freq="quarterly", curr_date=None):
            guard(ticker)
            period = "years" if freq == "annual" else "quarters"
            book = bundle["statements"][kind]
            rows = book[period][-4:]
            # Keep every labelled non-null field, but do not send the provider's
            # unrelated null banking/insurance fields or repeat keys per period.
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "Line item / provider field",
                    *[
                        f"{r['yearReport']}-Q{r.get('lengthReport', 5)} public:{str(r['publicDate'])[:10]}"
                        for r in rows
                    ],
                ]
            )
            for key, title in book["fields"].items():
                if any(r.get(key) is not None for r in rows):
                    writer.writerow([f"{title} [{key}]", *[r.get(key, "") for r in rows]])
            return (
                "Vietcap financial statements; raw VND except per-share/ratio/share-count fields. Periods as reported; do not assume CFO quarter vs YTD. Empty cell is missing, not zero.\n"
                + output.getvalue()
            )

        return read

    def fundamentals(ticker, curr_date):
        guard(ticker)
        return capsule(bundle)

    def news(ticker, start_date, end_date):
        guard(ticker)
        rows = [r for r in bundle.get("news", []) if start_date <= r["published"][:10] <= end_date]
        return json.dumps(
            {
                "source": "Vietcap public company-news feed",
                "coverage": "Headlines/short summaries only; do not claim to have read full articles. No items means no coverage, not no news.",
                "items": rows,
            },
            ensure_ascii=False,
        )

    def macro(indicator, asof, look_back_days=None):
        if indicator.startswith("vn_"):
            from desktop.macro_vietnam import tool

            return tool(
                bundle.get("domestic_macro", {"asof": bundle["analysis_date"]}),
                indicator,
                asof,
                look_back_days,
            )
        # Existing FRED aliases retain their original country/series semantics.
        return router.VENDOR_METHODS["get_macro_indicators"]["fred"](
            indicator, asof, look_back_days
        )

    def insider(ticker, as_of_date=None):
        guard(ticker)
        from desktop.insider_vietnam import tool

        data = bundle.get("insider_dealings")
        if not data:
            return json.dumps(
                {
                    "status": "unavailable",
                    "scope": "No domestic insider snapshot in this saved analysis; missing coverage is not no activity",
                }
            )
        return tool(data, ticker, as_of_date or bundle["analysis_date"])

    methods = {
        "get_insider_transactions": insider,
        "get_macro_indicators": macro,
        "get_stock_data": prices,
        "get_indicators": indicator,
        "get_fundamentals": fundamentals,
        "get_balance_sheet": statement("balance"),
        "get_cashflow": statement("cash"),
        "get_income_statement": statement("income"),
        "get_news": news,
    }
    try:
        for method, fn in methods.items():
            prior[method] = router.VENDOR_METHODS[method].get("vietnam")
            router.VENDOR_METHODS[method]["vietnam"] = fn

        def verified(ticker, asof, look_back_days=30, indicators=None):
            guard(ticker)
            if asof != bundle["analysis_date"]:
                return (
                    "Requested date differs from canonical snapshot; use the current analysis date."
                )
            return "Verified canonical VN snapshot (same source as other VN tools):\n" + capsule(
                bundle
            )

        tools.build_verified_market_snapshot = verified
        yield
    finally:
        tools.build_verified_market_snapshot = original_snapshot
        for method, old in prior.items():
            if old is None:
                router.VENDOR_METHODS[method].pop("vietnam", None)
            else:
                router.VENDOR_METHODS[method]["vietnam"] = old


@contextmanager
def settlement_scope(cfg):
    """Route original outcome scoring to VN closes too, without changing scoring."""
    if not cfg.get("desktop_vietnam", {}).get("enabled"):
        yield
        return
    from tradingagents.memory import settlement

    original = settlement.get_closes

    def closes(ticker, start, end):
        if not is_vietnam(ticker):
            return original(ticker, start, end)
        asof = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
        data = load(ticker, asof, cfg["data_cache_dir"], prices_only=True)
        rows = [r for r in data["prices"] if start <= r["date"] < end]
        # A truncated history must never settle a different holding window.
        if rows and data["prices"][0]["date"] > start:
            raise ValueError("Lịch sử Việt Nam không đủ cho cửa sổ settlement; giữ pending.")
        return pd.Series(
            [r["close"] for r in rows], index=pd.to_datetime([r["date"] for r in rows]), dtype=float
        )

    settlement.get_closes = closes
    try:
        yield
    finally:
        settlement.get_closes = original
