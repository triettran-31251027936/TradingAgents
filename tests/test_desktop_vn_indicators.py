"""VN tools preserve upstream formulas and the inclusive calendar-day contract."""

import ast
import inspect
import re
from datetime import date, timedelta

import pandas as pd
import pytest

from desktop import technical_vietnam as vn, vietnam
from tradingagents.dataflows import router
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.vendors.yahoo import market


@pytest.fixture
def bundle():
    frame = pd.DataFrame({
        "date": pd.bdate_range("2025-06-02", periods=320).strftime("%Y-%m-%d"),
        "open": [20000 + n * 9 for n in range(320)],
        "close": [20020 + n * 9 + (n % 7) * 10 for n in range(320)],
        "high": [20120 + n * 9 + (n % 7) * 10 for n in range(320)],
        "low": [19900 + n * 9 for n in range(320)],
        "volume": [100000 + (n % 13) * 1000 for n in range(320)],
    })
    return {"ticker": "VHM.VN", "analysis_date": frame.date.iloc[-1],
            "prices": frame.to_dict("records")}


def numbers(text):
    return {m[1]: float(m[2]) for m in re.finditer(
        r"^(\d{4}-\d{2}-\d{2}): (-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$", text, re.M
    )}


def test_supported_names_are_exactly_the_upstream_set():
    tree = ast.parse(inspect.getsource(market.get_stock_stats_indicators_window))
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "best_ind_params" for t in n.targets))
    assert set(vn.INDICATORS) == set(ast.literal_eval(assignment.value))


@pytest.mark.parametrize("name", vn.INDICATORS)
def test_all_indicators_match_original_upstream_on_same_candles(bundle, monkeypatch, name):
    rows = bundle["prices"]
    def load(*args):
        frame = pd.DataFrame(rows).rename(columns={"date": "Date"})
        frame["Date"] = pd.to_datetime(frame["Date"])
        return frame
    monkeypatch.setattr(market, "load_ohlcv", load)
    day = bundle["analysis_date"]
    native = market.get_stock_stats_indicators_window("VHM.VN", name, day, 30)
    with vietnam.vendor_scope(bundle):
        actual = router.VENDOR_METHODS["get_indicators"]["vietnam"]("VHM.VN", name, day, 30)
    assert numbers(actual) == pytest.approx(numbers(native))
    dates = re.findall(r"^(\d{4}-\d{2}-\d{2}):", actual, re.M)
    assert len(dates) == 31 and dates[0] == day
    assert dates[-1] == (date.fromisoformat(day) - timedelta(days=30)).isoformat()
    assert "calendar" in actual
    if name in ("rsi", "mfi"):
        assert "not VND" in actual
    if name == "mfi":
        assert "fraction (0–1)" in actual and "× 100" in actual
        assert all(0 <= value <= 1 for value in numbers(actual).values())


def test_no_candle_empty_range_and_date_guards(bundle):
    weekend = next(d for d in (date.fromisoformat(bundle["analysis_date"]) - timedelta(days=n)
                              for n in range(7)) if d.weekday() == 6)
    text = vn.indicator(bundle, "rsi", weekend.isoformat(), 0)
    assert not numbers(text) and "not proof of a market holiday" in text
    for bad in [-1, True, "30", 1.5]:
        with pytest.raises(ValueError, match="calendar"):
            vn.indicator(bundle, "rsi", bundle["analysis_date"], bad)
    with pytest.raises(ValueError, match="after"):
        vn.indicator(bundle, "rsi", "2099-01-01")
    with pytest.raises(NoMarketDataError):
        vn.indicator(bundle, "rsi", "2020-01-01")
    with pytest.raises(ValueError, match="Unsupported"):
        vn.indicator(bundle, "unknown", bundle["analysis_date"])
