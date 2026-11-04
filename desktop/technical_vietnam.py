"""Original stockstats indicators on the canonical VN completed-candle series."""

from datetime import date, timedelta

import pandas as pd
from stockstats import wrap

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.vendors.yahoo.ohlcv import _assert_ohlcv_not_stale

INDICATORS = (
    "close_50_sma", "close_200_sma", "close_10_ema", "macd", "macds", "macdh",
    "rsi", "boll", "boll_ub", "boll_lb", "atr", "vwma", "mfi",
)


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
    return (
        f"# Stock data for {bundle['ticker']} from {start} to {end}\n"
        f"# Total records: {len(rows)}\n"
        "Source: Vietcap; prices VND/share, volume shares; completed candles only.\n"
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
    return (
        f"## {name} values from {start.isoformat()} to {asof}:\n\n"
        "Source: Vietcap completed OHLCV; original stockstats formula/default window.\n"
        f"Unit: {unit}. Window: inclusive calendar dates; no candle is not a zero.\n"
        + "\n".join(lines) + "\n"
    )
