"""Canonical VN data and deterministic facts shared by every original graph role.

Independent HTTP adapter to public Vietcap endpoints. No vnstock code or field
mapping is distributed: statement titles come from the provider's metrics API.
No credentials, impersonation, rate-limit bypass or fallback to US fundamentals.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
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

from tradingagents.dataflows.errors import NoMarketDataError

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CHART = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
FINANCE = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company"
VERSION = 39
VERIFIED_SNAPSHOT_PREFIX = "Verified canonical VN snapshot (same source as other VN tools):\n"
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


def normalize_prices(raw, asof, now=None, start=None, *, reconcile=None):
    now = now or datetime.now(TZ)
    if start is not None:
        date.fromisoformat(start)
        if start > asof:
            raise ValueError("OHLCV start date is after analysis date")
    items = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(items, dict) or not all(k in items for k in ("t", "o", "h", "l", "c", "v")):
        raise ValueError("Nguồn không trả về OHLCV đúng schema.")
    sizes = {len(items[k]) for k in ("t", "o", "h", "l", "c", "v")}
    if len(sizes) != 1:
        raise ValueError("Các cột OHLCV khác độ dài.")
    rows = []
    for t, o, h, low, c, v in zip(*(items[k] for k in ("t", "o", "h", "l", "c", "v")), strict=True):
        stamp = pd.to_datetime(int(t), unit="s", utc=True).tz_convert(TZ).strftime("%Y-%m-%d")
        if (start is not None and stamp < start) or stamp > asof or (stamp == now.date().isoformat() and now.time() < day_time(15, 0)):
            continue  # a live partial daily candle is not a completed session
        nums = [_number(x) for x in (o, h, low, c, v)]
        if any(x is None for x in nums) or min(nums[:4]) <= 0 or nums[4] < 0:
            raise ValueError("OHLCV chứa giá/khối lượng không hợp lệ; không nội suy.")
        row = dict(
            zip(("date", "open", "high", "low", "close", "volume"), [stamp, *nums], strict=True)
        )
        if not (nums[2] <= min(nums[0], nums[3]) <= max(nums[0], nums[3]) <= nums[1]):
            row = reconcile(row) if reconcile else None
            if row is None:
                raise ValueError(f"OHLCV vi phạm quan hệ high/low tại {stamp}; chưa có nến đối chiếu.")
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    if len({r["date"] for r in rows}) != len(rows):
        raise ValueError("Nguồn trả về hai nến cùng ngày; cần đối soát.")
    if not rows:
        raise ValueError("Không có nến hoàn tất vào/trước ngày phân tích.")
    return rows


def analysis_history(rows, asof, prices_only=False):
    """Retain the upstream five-year analysis horizon; execution stays compact."""
    if prices_only:
        return rows
    start = (pd.Timestamp(asof) - pd.DateOffset(years=5)).date().isoformat()
    return [row for row in rows if start <= row["date"] <= asof]


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
        key = (
            row.get("newsId")
            or row.get("id")
            or digest([row.get("publicDate"), row.get("newsTitle"), row.get("newsSourceLink")])
        )
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


def collect_news(fetch, ticker, start, end, limit):
    """Bound the source requests by the configured upstream article count.

    A bounded feed is a sample even when the provider says its last page was
    reached. Keep coverage metadata instead of interpreting an empty page as
    proof that no company news exists.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("news_article_limit must be a positive integer")
    rows = []
    scanned = 0
    pages = 0
    seen = set()
    complete = False
    failed = False
    while scanned < limit:
        # Keep page size constant for offset-based pagination. Only the final
        # local selection is trimmed when the configured count is not /30.
        size = min(30, limit)
        url = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker={ticker}&fromDate={start}&toDate={end}&languageId=1&page={pages}&size={size}"
        raw = fetch("news" if pages == 0 else f"news_page_{pages}", url)
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            failed = True
            break
        content = data["content"]
        pages += 1
        for row in normalize_news(raw, start, end, url):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
        scanned += len(content)
        if data.get("last") is True or len(content) < size:
            complete = True
            break
        if not content:
            break
    rows.sort(key=lambda row: row["published"], reverse=True)
    selected = rows[:limit]
    return selected, {
        "status": "partial"
        if failed and selected
        else "unavailable"
        if failed
        else "available"
        if selected
        else "empty_sample",
        "start_date": start,
        "end_date": end,
        "article_limit": limit,
        "pages_received": pages,
        "raw_rows_received": scanned,
        "items_retained": len(selected),
        "provider_last_page_reached": complete,
        "limit_reached": not complete and not failed and scanned >= limit,
        "coverage": "Bounded company-news headlines/short summaries from one aggregator; no full articles or exhaustive coverage. Empty sample does not mean no news.",
    }


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
    share_registry=None,
    price_source="Vietcap OHLCV",
    price_basis=None,
):
    df = pd.DataFrame(rows).set_index("date")
    close = df["close"]
    ret = close.pct_change(fill_method=None).dropna()
    stock = wrap(df.copy())
    metrics = {}
    missing = []

    def add(name, value, unit, formula, source=price_source, asof=None):
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
        share_registry=share_registry,
        price_basis=price_basis,
    )
    if valuation_proof is not None:
        valuation_proof.update(proof)
    return _safe(metrics), missing


def _read_saved_quantitative_snapshot(root, path, ticker, start, asof):
    try:
        if (
            path.name.endswith("_raw.json") or path.is_symlink()
            or (path.parent / ".publishing").exists()
            or not path.resolve().is_relative_to(root.resolve())
            or not 0 < path.stat().st_size <= 12 * 1024 * 1024
        ):
            return None
        snapshot = json.loads(path.read_text())
        if (
            snapshot["ticker"] == ticker and start <= snapshot["analysis_date"] <= asof
            and snapshot["fingerprint"] == digest({
                k: v for k, v in snapshot.items() if k not in ("sources", "fingerprint")
            })
        ):
            return snapshot
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def saved_quantitative_snapshots(ticker, asof, folder, reports=None, *, start=None):
    """Yield bounded, intact local snapshots for an exact date or short date range."""
    start = start or asof
    stock = symbol(ticker)
    day = date.fromisoformat(start)
    end = date.fromisoformat(asof)
    paths = []
    while day <= end:
        paths.extend((folder, p) for p in folder.glob(f"{stock}_{day.isoformat()}_v*_news*.json"))
        day += timedelta(days=1)
    if reports is not None:
        paths.extend((reports, p) for p in reports.glob(f"reports/{ticker}_*/quantitative.json"))
        paths.extend((reports, p) for p in reports.glob(f"backtest/**/{ticker}_*/quantitative.json"))
    for root, path in paths:
        snapshot = _read_saved_quantitative_snapshot(root, path, ticker, start, asof)
        if snapshot:
            yield snapshot


def load(
    ticker,
    asof,
    cache_dir,
    *,
    refresh=False,
    session=None,
    prices_only=False,
    price_history_limit=None,
    news_article_limit=20,
    global_news_article_limit=10,
    global_news_lookback_days=7,
    report_dir=None,
):
    ticker = symbol(ticker)
    date.fromisoformat(asof)
    folder = Path(cache_dir) / "vietnam"
    if price_history_limit is not None and (
        not prices_only or type(price_history_limit) is not int or not 2 <= price_history_limit <= 600
    ):
        raise ValueError("A bounded price history requires prices_only and 2–600 candles")
    price_count = (price_history_limit or 600) if prices_only else 1500
    if type(news_article_limit) is not int or news_article_limit < 1:
        raise ValueError("news_article_limit must be a positive integer")
    if (
        type(global_news_article_limit) is not int
        or global_news_article_limit < 1
        or type(global_news_lookback_days) is not int
        or global_news_lookback_days < 0
    ):
        raise ValueError("Global news requires positive article limit and nonnegative lookback")
    path = folder / (
        f"{ticker}_{asof}_v{VERSION}"
        + (
            ("_prices.json" if price_history_limit is None else f"_prices{price_count}.json")
            if prices_only
            else f"_news{news_article_limit}_global{global_news_article_limit}x{global_news_lookback_days}.json"
        )
    )
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

        from desktop.technical_vietnam import anchor_history, check_freshness, reconcile_candle

        price_reconciliations = []

        def reconcile(row):
            if len(price_reconciliations) >= 8:
                return None
            result = reconcile_candle(ticker, row, fetch)
            if result:
                corrected, receipt = result
                price_reconciliations.append(receipt)
                return corrected
            return None

        history_start = None if prices_only else (pd.Timestamp(asof) - pd.DateOffset(years=5)).date().isoformat()
        prices = normalize_prices(
            fetch(
                "prices",
                CHART,
                {
                    "timeFrame": "ONE_DAY",
                    "symbols": [ticker],
                    "to": int(cutoff.timestamp()),
                    "countBack": price_count,
                },
                True,
            ),
            asof,
            start=history_start,
            reconcile=reconcile,
        )
        prices = analysis_history(prices, asof, prices_only)

        check_freshness(prices, ticker + ".VN", asof)
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
        price_basis = {}
        if not prices_only and asof < datetime.now(TZ).date().isoformat():
            prices, price_basis = anchor_history(prices, ticker, fetch)
            if price_basis["status"] == "unverified" and exchange == "HNX":
                from desktop import hnx_prices
                from desktop.technical_vietnam import anchor_hnx_history

                try:
                    observed = hnx_prices.observation(ticker, date.fromisoformat(prices[-1]["date"]))
                    prices, price_basis = anchor_hnx_history(prices, observed)
                except (ValueError, OSError, requests.RequestException, subprocess.SubprocessError):
                    pass
                else:
                    raw["HNX historical price anchor"] = observed
                    sources.append({
                        "name": "HNX historical price anchor",
                        "url": observed["url"],
                        "retrieved_at": datetime.now(UTC).isoformat(),
                        "sha256": observed["sha256"],
                    })
            if price_basis["status"] == "unverified":
                warnings.append(
                    "Historical price anchor unavailable or inconsistent; the Vietcap scale "
                    "is retained and absolute levels may differ from actual traded prices."
                )
        if price_reconciliations:
            from desktop.technical_vietnam import _SOURCE_BASIS

            warning = (
                f"{len(price_reconciliations)} inconsistent Vietcap OHLC candles reconciled "
                "against same-session DNSE chart data; Vietcap volume retained."
            )
            warnings.append(warning)
            price_basis["description"] = (
                price_basis.get("description", _SOURCE_BASIS) + " " + warning
            )
        news_start = (date.fromisoformat(asof) - timedelta(days=7)).isoformat()
        news, news_coverage = (
            ([], {})
            if prices_only
            else collect_news(fetch, ticker, news_start, asof, news_article_limit)
        )
        if news_coverage.get("status") in ("partial", "unavailable"):
            warnings.append(
                "Company news source incomplete/unavailable; empty sample is not no news"
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
                    "countBack": 1500,
                },
            )
        )
        try:
            benchmark = normalize_prices(benchmark_raw, asof, start=history_start) if benchmark_raw else []
            benchmark = analysis_history(benchmark, asof)
            if benchmark:
                check_freshness(benchmark, "VNINDEX.VN", asof)
        except (ValueError, NoMarketDataError):
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
        from desktop.share_registry import load as load_registry

        registry = None if prices_only else load_registry(ticker, asof, cache_dir)
        if registry:
            sources.append(
                {
                    "name": "VSDC registered-share history",
                    "url": registry.get("source_url"),
                    "retrieved_at": registry.get("observed_at"),
                    "sha256": registry.get("sha256"),
                    "status": registry["status"],
                }
            )
            if registry.get("notice_url"):
                sources.append({
                    "name": "Công bố số cổ phiếu đăng ký · VSDC",
                    "url": registry["notice_url"],
                    "published": registry["notice_published"],
                    "retrieved_at": registry["observed_at"],
                    "sha256": registry["notice_sha256"],
                    "status": registry["status"],
                })
            if issuer_change := registry.get("issuer_capital_change"):
                sources.append({
                    "name": "Công bố thay đổi vốn điều lệ · " + issuer_change["issuer"],
                    "url": issuer_change["url"],
                    "published": issuer_change["published"],
                    "retrieved_at": registry["observed_at"],
                    "sha256": issuer_change["sha256"],
                    "status": registry["status"],
                })
            if registry["status"] != "available":
                warnings.append(
                    "VSDC share registry: " + registry.get("reason", registry["status"])
                )
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
            share_registry=registry,
            price_basis=price_basis,
            price_source=(
                f"Vietcap OHLCV / {price_basis.get('source', 'CafeF')} session-price anchor"
                if price_basis.get("status") == "anchored"
                else "Vietcap OHLCV"
            ),
        )
        if not prices_only and prices[-1]["date"] == asof:
            from desktop.valuation import PROVIDER_OBSERVATIONS, replay_provider_observations

            needed = {key for key in PROVIDER_OBSERVATIONS if metrics[key]["value"] is None}
            price_verified = (
                price_basis.get("status") == "anchored" and price_basis.get("date") == asof
                or valuation_observed_at[:10] == asof
            )
            saved = (
                replay_provider_observations(
                    ticker + ".VN", asof, prices[-1]["close"] if price_verified else None,
                    valuation_proof["statement_ttm_end"], folder, Path(report_dir) if report_dir else None,
                    needed=needed, shares=metrics["shares_marketcap_observed"]["value"],
                )
                if needed else None
            )
            if saved:
                metrics.update(saved["fields"])
                valuation_proof["provider_archive"] = {
                    **{key: value for key, value in saved.items() if key != "fields"},
                    "restored_fields": sorted(saved["fields"]),
                }
                sources.append({
                    "name": "Quan sát định giá Vietcap đã lưu",
                    "url": saved["source_url"],
                    "observed_at": saved["observed_at"],
                    "sha256": saved["source_sha256"],
                })
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
        debt_notes = {}
        if not prices_only:
            from desktop.debt_notes import load as load_debt_notes

            debt_notes = load_debt_notes(ticker, asof, metrics)
            if debt_notes:
                sources.append(debt_notes["source"])
        from desktop.financial_metrics import reconcile

        reconciliation = {
            kind: {
                key: reconcile(book[kind], key)
                for key in book[kind]["fields"]
                if any(
                    term in book[kind]["fields"][key].lower()
                    for term in (
                        "net sales", "operating activities", "purchases of fixed",
                        "accounting profit/(loss) before tax", "interest expenses",
                        "interest paid", "depreciation and amortization",
                    )
                )
            }
            for kind in ("income", "cash")
        }
        domestic_macro = {}
        if not prices_only:
            from desktop import macro_vietnam

            domestic_macro = macro_vietnam.load(asof, cache_dir)
            from desktop.macro_releases import load as load_releases

            releases = load_releases(asof, cache_dir)
            domestic_macro["official_releases"] = releases
            macro_vietnam.apply_cpi_yoy_release(domestic_macro, releases)
            metrics.update(macro_vietnam.metrics(domestic_macro))
            metrics.update(releases["metrics"])
            domestic_macro["warnings"].extend(releases["warnings"])
            from desktop import monetary_vietnam

            monetary = monetary_vietnam.load(asof, cache_dir)
            domestic_macro["monetary_releases"] = monetary
            metrics.update(monetary["metrics"])
            domestic_macro["warnings"].extend(monetary["warnings"])
            sources.extend(
                {"name": "Dated NHNN briefing evidence", **source} for source in monetary["sources"]
            )
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
                    "url": bank_fx.get("source_url", fx_vietnam.URL),
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
                        "server_modified_at": source.get("server_modified_at"),
                        "content_timestamp": source.get("content_timestamp"),
                        "source_basis": source.get("source_basis"),
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
            observed = None
            first_observed = insider_dealings.get("first_observed_at") or ""
            exact_dealings = (
                insider_dealings["status"] == "available"
                and first_observed[:10] == asof
                and insider_dealings.get("source_total") == insider_dealings.get("retrieved_count")
            )
            if not exact_dealings and asof < datetime.now(TZ).date().isoformat():
                observed = insider_vietnam.replay_observed(
                    ticker + ".VN", asof, folder, Path(report_dir) if report_dir else None
                )
                if observed and observed["asof"] == asof:
                    insider_dealings = observed
            from desktop.share_registry import load_transfers

            try:
                transfers = load_transfers(ticker, asof, cache_dir)
            except (OSError, ValueError) as exc:
                transfers = {"status": "unavailable", "records": [], "warnings": [type(exc).__name__]}
            insider_dealings = insider_vietnam.corroborate_historical(
                insider_dealings, transfers, cache_dir
            )
            prior_registration = insider_dealings.get("registration_snapshot", {}).get("observed_at")
            if observed and observed["asof"] != asof and (
                not prior_registration
                or datetime.fromisoformat(observed["retrieved_at"]) > datetime.fromisoformat(prior_registration)
            ):
                insider_dealings["registration_snapshot"] = insider_vietnam.registration_observation(observed)
                sources.append({
                    "name": "Quan sát đăng ký giao dịch đã lưu · CafeF",
                    "url": observed["source_page"],
                    "observed_at": observed["retrieved_at"],
                    "sha256": observed["source_sha256"],
                    "status": "stale",
                })
            sources.extend(
                {"name": "Công bố kết quả giao dịch · CafeF dẫn HOSE", **row}
                for row in insider_dealings.get("result_evidence", [])
            )
            if ticker == "PVS" and insider_dealings["status"] in ("withheld", "unavailable"):
                from desktop import ptsc_dealings

                try:
                    issuer = ptsc_dealings.load(asof)
                except (OSError, ValueError, requests.RequestException) as exc:
                    warnings.append("PTSC shareholder disclosures: " + type(exc).__name__)
                else:
                    if issuer["records"]:
                        if "registration_snapshot" in insider_dealings:
                            issuer["registration_snapshot"] = insider_dealings["registration_snapshot"]
                        sources.extend(
                            {"name": "Công bố giao dịch nội bộ · PTSC", "status": "available", **row}
                            for row in issuer["issuer_sources"]
                        )
                        insider_dealings = {**issuer, "cafef_status": insider_dealings["status"]}
                    else:
                        warnings.extend(issuer["warnings"])
            insider_dealings["vsdc_transfers"] = transfers
            warnings.extend(transfers["warnings"])
            sources.extend(
                {
                    "name": "Công bố chuyển quyền sở hữu · VSDC",
                    "url": row["url"],
                    "published": row["published"],
                    "observed_at": transfers["observed_at"],
                    "sha256": row["sha256"],
                    "status": transfers["status"],
                }
                for row in transfers["records"]
            )
            metrics.update(insider_vietnam.metrics(insider_dealings))
            missing = missing_metrics(metrics)
            warnings.extend(insider_dealings.get("warnings", []))
            sources.append(
                {
                    "name": "PTSC insider disclosures index"
                    if "cafef_status" in insider_dealings else "CafeF shareholder dealings",
                    "url": insider_dealings["source_url"],
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
        market_news = {}
        if not prices_only:
            from desktop import market_news_vietnam

            try:
                market_news = market_news_vietnam.load(
                    asof,
                    cache_dir,
                    limit=global_news_article_limit,
                    look_back_days=global_news_lookback_days,
                )
            except OSError as exc:
                market_news = {
                    "status": "unavailable",
                    "records": [],
                    "sources": [],
                    "scope": market_news_vietnam.SCOPE,
                    "warnings": ["Market-news cache unavailable: " + type(exc).__name__],
                }
            warnings.extend(market_news.get("warnings", []))
            sources.extend(market_news.get("sources", []))
        bundle = {
            "exchange": exchange,
            "company_name": instrument.get("enOrganName") or instrument.get("organName") or ticker,
            "version": VERSION,
            "ticker": ticker + ".VN",
            "analysis_date": asof,
            "currency": "VND",
            "provider": "Vietcap public market data",
            "prices": prices,
            "price_basis": price_basis,
            "benchmark": benchmark,
            "history_coverage": {
                "requested_candles": price_count,
                "requested_start": history_start,
                "ohlc_reconciliations": price_reconciliations,
                "completed_rows": len(prices),
                "first_date": prices[0]["date"],
                "last_date": prices[-1]["date"],
                "benchmark_completed_rows": len(benchmark),
                "scope": "Bounded provider history; listing age, missing sessions and historical revisions may limit coverage. Not an unrevised vintage or a guarantee of every market session.",
            },
            "domestic_macro": domestic_macro,
            "insider_dealings": insider_dealings,
            "domestic_social": domestic_social,
            "statements": book,
            "metrics": metrics,
            "financial_reconciliation": reconciliation,
            "company_profile": company_profile,
            "share_registry": registry,
            "valuation_observed_at": valuation_observed_at,
            "valuation_reconciliation": valuation_proof,
            "bank_reconciliation": bank_proof,
            "debt_notes": debt_notes,
            "missing": missing,
            "warnings": warnings,
            "news": news,
            "news_coverage": news_coverage,
            "market_news": market_news,
            "sources": sources,
            "basis": [
                price_basis.get(
                    "description",
                    "Prices as returned by source; historical corporate-action adjustments may apply.",
                ),
                "Published-date filtering is date-level. Restated accounts are not a point-in-time archive.",
                "Statement values are raw VND except per-share/ratio/share-count fields. No conversion from billions.",
                "TTM flows require contiguous quarters and empirical quarter/FY reconciliation within0.5%; methodology can change. See financial_reconciliation. Raw CFO is not automatically TTM FCF.",
            ],
        }
        bundle = _safe(bundle)
        bundle["fingerprint"] = digest({k: v for k, v in bundle.items() if k != "sources"})
        if not prices_only:
            from desktop.valuation import archive_provider_observation

            previous = _read_saved_quantitative_snapshot(folder, path, bundle["ticker"], asof, asof)
            if previous:
                archive_provider_observation(previous, folder)
            archive_provider_observation(bundle, folder)
        write_json(path, bundle)
        write_json(folder / (path.stem + "_raw.json"), raw)
        return bundle


def _metric_references(section, canonical):
    """Replace identical nested metric records with references to the top-level map."""
    metrics = section.get("metrics", {})
    references = [
        name
        for name, metric in metrics.items()
        if name in canonical and digest(metric) == digest(canonical[name])
    ]
    if not references:
        return section
    return {
        **section,
        "metrics": {name: metric for name, metric in metrics.items() if name not in references},
        "metric_keys_in_canonical_metrics": references,
    }


def capsule(bundle, *, compact=False):
    from desktop.macro_vietnam import summary as macro_summary

    decoded = {
        k: bundle[k]
        for k in [
            "ticker",
            "company_name",
            "company_profile",
            "exchange",
            "analysis_date",
            "currency",
            "provider",
            "news_coverage",
            "bank_reconciliation",
            "valuation_reconciliation",
            "metrics",
            "history_coverage",
            "price_basis",
            "missing",
            "warnings",
            "basis",
            "fingerprint",
        ]
        if k in bundle
    }
    if bundle.get("debt_notes"):
        decoded["debt_notes"] = bundle["debt_notes"]
    canonical = bundle.get("metrics", {})
    if bundle.get("domestic_macro"):
        macro = macro_summary(bundle["domestic_macro"])
        if compact and "official_releases" in macro:
            macro["official_releases"] = _metric_references(macro["official_releases"], canonical)
        decoded["domestic_macro"] = macro
    if bundle.get("insider_dealings"):
        from desktop.insider_vietnam import summary as insider_summary

        insider = insider_summary(bundle["insider_dealings"])
        if compact:
            insider = _metric_references(insider, canonical)
            if "registration_snapshot" in insider:
                insider["registration_snapshot"] = _metric_references(
                    insider["registration_snapshot"], canonical
                )
        decoded["insider_dealings"] = insider
    if bundle.get("domestic_social"):
        from desktop.social_vietnam import summary as social_summary

        decoded["domestic_social"] = social_summary(bundle["domestic_social"])
    if bundle.get("market_news"):
        from desktop.market_news_vietnam import summary as market_news_summary

        decoded["market_news"] = market_news_summary(bundle["market_news"])
    return json.dumps(decoded, ensure_ascii=False, allow_nan=False)


@contextmanager
def vendor_scope(bundle, cache_dir=None):
    """Keep upstream tools/nodes, replace only their VN data transport in this job."""
    if bundle is None:
        yield
        return
    from tradingagents.agents import tools
    from tradingagents.dataflows import router

    original_snapshot = tools.build_verified_market_snapshot
    prediction_vendors = router.VENDOR_METHODS["get_prediction_markets"]
    original_prediction = prediction_vendors["polymarket"]
    prior = {}

    def guard(ticker):
        if symbol(ticker) != symbol(bundle["ticker"]):
            raise ValueError("Snapshot thuộc mã khác.")

    def prices(ticker, start, end):
        guard(ticker)
        from desktop.technical_vietnam import prices as read_prices

        return read_prices(bundle, start, end)

    def indicator(ticker, name, asof, look_back_days=30):
        guard(ticker)
        from desktop.technical_vietnam import indicator as read_indicator

        return read_indicator(bundle, name, asof, look_back_days)

    def statement(kind):
        def read(ticker, freq="quarterly", curr_date=None):
            guard(ticker)
            from desktop.statements_vietnam import read as read_statement

            return read_statement(bundle, kind, freq, curr_date)

        return read

    def fundamentals(ticker, curr_date):
        guard(ticker)
        date.fromisoformat(curr_date)
        if curr_date != bundle["analysis_date"]:
            raise ValueError(
                "Fundamentals date must match the canonical analysis snapshot; prepare a separate dated snapshot"
            )
        return capsule(bundle)

    def news(ticker, start_date, end_date):
        guard(ticker)
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
        if start_date > end_date or end_date > bundle["analysis_date"]:
            raise ValueError("News date range is invalid or after the analysis snapshot")
        rows = [r for r in bundle.get("news", []) if start_date <= r["published"][:10] <= end_date]
        coverage = bundle.get("news_coverage") or {}
        covered = (
            bool(coverage)
            and start_date >= coverage["start_date"]
            and end_date <= coverage["end_date"]
        )
        return json.dumps(
            {
                "source": "Vietcap public company-news feed",
                "coverage": "Headlines/short summaries only; do not claim to have read full articles. No items means no coverage, not no news.",
                "requested_window": {"start_date": start_date, "end_date": end_date},
                "snapshot_coverage": coverage,
                "requested_window_within_snapshot": covered,
                "window_warning": None
                if covered
                else "Requested range extends beyond, or is not established by, the saved feed window. Returned items are only the overlapping bounded sample.",
                "items": rows,
            },
            ensure_ascii=False,
        )

    def global_news(asof, look_back_days=None, limit=None):
        from tradingagents.dataflows.config import get_config, run_config
        from desktop.market_news_vietnam import tool

        config = get_config()
        days = config["global_news_lookback_days"] if look_back_days is None else look_back_days
        count = config["global_news_article_limit"] if limit is None else limit
        target = date.fromisoformat(asof)
        if target.isoformat() != asof or asof > bundle["analysis_date"]:
            raise ValueError("Global news date is invalid or after the analysis snapshot")
        rss = tool(bundle.get("market_news", {}), asof, days, count)
        method = "get_global_news"
        configured = config.get("desktop_global_news_vendor", "default")
        chain = [item.strip() for item in configured.split(",") if item.strip() not in ("", "default", "vietnam")]
        if not chain:
            chain = [name for name in router.VENDOR_METHODS[method] if name != "vietnam"]
        config["tool_vendors"][method] = ",".join(chain)
        try:
            with run_config(config):
                upstream = router.route_to_vendor(method, asof, days, count)
        except Exception as exc:
            upstream = f"DATA_UNAVAILABLE: configured global news source ({type(exc).__name__})"
        return json.dumps(
            {
                "upstream": upstream,
                "upstream_vendors": chain,
                "vneconomy_rss": rss,
                "article_limit_per_section": count,
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
        if cache_dir is None:
            return router.VENDOR_METHODS["get_macro_indicators"]["fred"](
                indicator, asof, look_back_days
            )
        from desktop.fred_public import get_macro_data

        return get_macro_data(indicator, asof, look_back_days, cache_dir)

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
        "get_global_news": global_news,
    }
    def prediction(topic, limit=None, asof=None):
        from desktop.prediction_markets import tool

        day = asof or bundle["analysis_date"]
        if day > bundle["analysis_date"]:
            raise ValueError("Prediction market date is after the analysis snapshot")
        return tool(topic, limit, day, cache_dir)

    try:
        if cache_dir is not None:
            prediction_vendors["polymarket"] = prediction
        for method, fn in methods.items():
            prior[method] = router.VENDOR_METHODS[method].get("vietnam")
            router.VENDOR_METHODS[method]["vietnam"] = fn

        def verified(ticker, asof, look_back_days=30, indicators=None):
            guard(ticker)
            if asof != bundle["analysis_date"]:
                return (
                    "Requested date differs from canonical snapshot; use the current analysis date."
                )
            return VERIFIED_SNAPSHOT_PREFIX + capsule(bundle)

        tools.build_verified_market_snapshot = verified
        yield
    finally:
        tools.build_verified_market_snapshot = original_snapshot
        prediction_vendors["polymarket"] = original_prediction
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
        # The upstream evaluator requests a future upper bound to leave room
        # for holidays. Archive what is observed now, not a future-dated price
        # snapshot, and let its unchanged holding-window checks decide readiness.
        asof = min(
            date.fromisoformat(end) - timedelta(days=1), datetime.now(TZ).date()
        ).isoformat()
        data = load(ticker, asof, cfg["data_cache_dir"], prices_only=True)
        rows = [r for r in data["prices"] if start <= r["date"] < end]
        # A truncated history must never settle a different holding window.
        if rows and data["prices"][0]["date"] > start:
            raise ValueError("Lịch sử Việt Nam không đủ cho cửa sổ settlement; giữ pending.")
        values = pd.Series(
            [r["close"] for r in rows],
            index=pd.to_datetime([r["date"] for r in rows]).tz_localize(TZ),
            dtype=float,
        )
        values.attrs["vn_snapshot_fingerprint"] = data.get("fingerprint")
        values.attrs["vn_source_receipts"] = [
            {key: row.get(key) for key in ("name", "retrieved_at", "sha256")}
            for row in data.get("sources", []) if row.get("name") == "prices"
        ]
        return values

    settlement.get_closes = closes
    try:
        from desktop.settlement_evidence import capture

        with capture(cfg, is_vietnam) as evidence:
            yield evidence
    finally:
        settlement.get_closes = original
