"""Original stockstats indicators on the canonical VN completed-candle series."""

import math
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

import pandas as pd
from stockstats import wrap

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.vendors.yahoo.ohlcv import _assert_ohlcv_not_stale

INDICATORS = (
    "close_50_sma", "close_200_sma", "close_10_ema", "macd", "macds", "macdh",
    "rsi", "boll", "boll_ub", "boll_lb", "atr", "vwma", "mfi",
)


_PRICE_HISTORY = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/PriceHistory.ashx"
_SOURCE_BASIS = "Vietcap prices on the provider's current adjustment basis. Historical closes may differ from actual session prices."
OHLC = ("open", "high", "low", "close")


def reconcile_candle(ticker, primary, fetch):
    """Use a dated DNSE observation only when both price-scale anchors agree."""
    from desktop.vietnam import TZ, normalize_prices

    day = date.fromisoformat(primary["date"])
    start = datetime.combine(day, time.min, TZ)
    end = datetime.combine(day, time.max, TZ)
    query = urlencode({
        "symbol": ticker, "resolution": "1D",
        "from": int(start.timestamp()), "to": int(end.timestamp()),
    })
    url = f"https://api.dnse.com.vn/chart-api/v2/ohlcs/stock?{query}"
    raw = fetch(f"DNSE OHLC reconciliation {day}", url)
    try:
        secondary, = normalize_prices(raw, day.isoformat(), start=day.isoformat())
        prices = {key: secondary[key] * 1000 for key in OHLC}
        # The chart projects prices in increments of 0.01 thousand VND.
        if any(
            abs(prices[key] - primary[key]) > min(10.001, 0.001 * primary[key])
            for key in ("open", "close")
        ):
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    repaired = {**primary, **prices}
    return repaired, {
        "date": primary["date"],
        "source": "DNSE public chart",
        "url": url,
        "method": "DNSE OHLC converted from thousand VND; opening and close agree within one displayed increment and 0.1%. Vietcap volume is retained.",
        "vietcap_ohlc": {key: primary[key] for key in OHLC},
        "dnse_ohlc_vnd": prices,
        "vietcap_volume": primary["volume"],
        "dnse_volume": secondary["volume"],
    }


def anchor_history(rows, ticker, fetch):
    """Convert a historical adjusted series to the last completed session's price scale."""
    anchor = rows[-1]
    day = date.fromisoformat(anchor["date"])
    query = urlencode({
        "Symbol": ticker, "StartDate": day.strftime("%m/%d/%Y"),
        "EndDate": day.strftime("%m/%d/%Y"), "PageIndex": 1, "PageSize": 1,
    })
    raw = fetch("CafeF historical price anchor", f"{_PRICE_HISTORY}?{query}")
    try:
        if raw["Success"] is not True or raw["Data"]["TotalCount"] != 1:
            raise ValueError("Expected one session")
        item, = raw["Data"]["Data"]
        if item["Symbol"] != ticker or item["Ngay"] != day.strftime("%d/%m/%Y"):
            raise ValueError("Mismatched ticker or session")
        fields = ("GiaMoCua", "GiaCaoNhat", "GiaThapNhat", "GiaDongCua", "GiaDieuChinh")
        values = [item[k] for k in fields]
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Invalid CafeF prices")
        opening, high, low, close, adjusted = [v * 1000 for v in values]
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("Invalid CafeF high/low")
        # CafeF displays adjusted closes at 0.01 thousand VND precision.
        # Require same-session alignment before inferring any scale factor.
        if not math.isclose(adjusted, anchor["close"], rel_tol=0, abs_tol=5.001):
            raise ValueError("Sources disagree on adjusted close")
        factor = close / anchor["close"]
        if any(
            not math.isclose(anchor[key] * factor, observed, rel_tol=0, abs_tol=10)
            for key, observed in zip(OHLC, (opening, high, low, close), strict=True)
        ):
            raise ValueError("Sources disagree on candle shape")
    except (KeyError, TypeError, ValueError, OverflowError):
        return rows, {"status": "unverified", "description": _SOURCE_BASIS}
    return [
        {**r, **{key: r[key] / anchor["close"] * close for key in OHLC}} for r in rows
    ], {
        "status": "anchored",
        "date": anchor["date"],
        "observed_close_vnd": close,
        "vietcap_adjusted_close_vnd": anchor["close"],
        "cafef_adjusted_close_vnd": adjusted,
        "scale_factor": factor,
        "description": (
            f"Vietcap OHLC rescaled to the CafeF actual close on {anchor['date']}. "
            "Earlier bars remain adjusted on that session's price scale. Volume is unchanged. "
            "This is not an archived vintage or a certified total-return series."
        ),
    }


def anchor_hnx_history(rows, observed):
    anchor = rows[-1]
    prices = observed["ohlc_vnd"]
    opening, high, low, close = (prices[key] for key in OHLC)
    if observed["date"] != anchor["date"] or not 0 < low <= min(opening, close) <= max(opening, close) <= high:
        raise ValueError("Invalid HNX session anchor")
    factor = close / anchor["close"]
    if any(abs(anchor[key] * factor - prices[key]) > 10 for key in OHLC):
        raise ValueError("HNX and Vietcap disagree on candle shape")
    scaled = rows if factor == 1 else [
        {**row, **{key: row[key] / anchor["close"] * close for key in OHLC}} for row in rows
    ]
    return scaled, {
        "status": "anchored",
        "date": anchor["date"],
        "observed_close_vnd": close,
        "vietcap_adjusted_close_vnd": anchor["close"],
        "scale_factor": factor,
        "source": "HNX",
        "description": (
            f"Vietcap OHLC anchored to the HNX published closing price on {anchor['date']}. "
            "Earlier bars remain adjusted on that session's price scale. Volume is unchanged. "
            "This is not an archived vintage or a certified total-return series."
        ),
    }


def check_freshness(rows, ticker, asof):
    if not rows:
        raise NoMarketDataError(ticker, detail="No completed candles in requested range")
    frame = pd.DataFrame(rows).rename(columns={"date": "Date"})
    _assert_ohlcv_not_stale(frame, asof, ticker)


def prices(bundle, start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("Price start date is after end date")
    if last > date.fromisoformat(bundle["analysis_date"]):
        raise ValueError("Price end date is after the canonical analysis snapshot")
    rows = [r for r in bundle["prices"] if start <= r["date"] <= end]
    check_freshness(rows, bundle["ticker"], end)
    earliest = min(r["date"] for r in bundle["prices"])
    coverage = (
        "Requested start precedes the earliest snapshot candle; returned rows are "
        "only the bounded overlap, not verified full history.\n"
        if start < earliest else ""
    )
    basis = bundle.get("price_basis", {}).get("description", _SOURCE_BASIS)
    return (
        f"# Stock data for {bundle['ticker']} from {start} to {end}\n"
        f"# Total records: {len(rows)}\n"
        "Source: Vietcap; prices VND/share, volume shares; completed candles only.\n"
        + basis + "\n"
        + coverage
        + pd.DataFrame(rows).to_csv(index=False)
    )


def indicator(bundle, name, asof, look_back_days=30):
    if name not in INDICATORS:
        raise ValueError("Unsupported indicator; choose from: " + ", ".join(INDICATORS))
    end = date.fromisoformat(asof)
    if end > date.fromisoformat(bundle["analysis_date"]):
        raise ValueError("Indicator date is after the canonical analysis snapshot")
    if type(look_back_days) is not int or look_back_days < 0:
        raise ValueError("look_back_days must be a nonnegative integer of calendar days")
    start = end - timedelta(days=look_back_days)
    rows = [r for r in bundle["prices"] if r["date"] <= asof]
    check_freshness(rows, bundle["ticker"], asof)
    # The upstream Yahoo implementation uses stockstats' own parameters and
    # formulas for every name, including its typical-price VWMA. Do not replace
    # one name with a close-price average having a different window.
    values = wrap(pd.DataFrame(rows).set_index("date"))[name]
    lines = []
    day = end
    while day >= start:
        key = day.isoformat()
        if key not in values.index:
            value = "N/A: No completed candle in snapshot (not proof of a market holiday)"
        else:
            number = values.loc[key]
            value = "N/A" if pd.isna(number) else str(number)
        lines.append(f"{key}: {value}")
        day -= timedelta(days=1)
    unit = (
        "fraction (0–1), not VND; stockstats raw MFI × 100 for conventional 80/20 thresholds"
        if name == "mfi"
        else "index (0–100), not VND" if name == "rsi" else "VND/share"
    )
    basis = bundle.get("price_basis", {}).get("description", _SOURCE_BASIS)
    return (
        f"## {name} values from {start.isoformat()} to {asof}:\n\n"
        "Source: Vietcap completed OHLCV; original stockstats formula/default window.\n"
        f"{basis}\n"
        f"Unit: {unit}. Window: inclusive calendar dates; no candle is not a zero.\n"
        + "\n".join(lines) + "\n"
    )
