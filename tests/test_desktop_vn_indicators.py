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


def test_price_tool_preserves_inclusive_end_and_typed_no_data(bundle):
    end = bundle["analysis_date"]
    with vietnam.vendor_scope(bundle):
        read = router.VENDOR_METHODS["get_stock_data"]["vietnam"]
        text = read("VHM.VN", end, end)
        assert "Total records: 1" in text and end in text
        assert "bounded overlap" in read("VHM.VN", "2020-01-01", end)
        with pytest.raises(NoMarketDataError):
            read("VHM.VN", "2020-01-01", "2020-01-31")
        with pytest.raises(ValueError, match="after"):
            read("VHM.VN", end, "2099-01-01")
        with pytest.raises(ValueError):
            read("VHM.VN", "2026-01-03", "2026-01-02")
        with pytest.raises(ValueError):
            read("VHM.VN", "not-a-date", end)


def test_same_original_staleness_boundary_for_price_and_indicator(bundle):
    last = date.fromisoformat(bundle["prices"][-1]["date"])
    bundle["analysis_date"] = (last + timedelta(days=11)).isoformat()
    with pytest.raises(NoMarketDataError, match="stale"):
        vn.prices(bundle, bundle["prices"][0]["date"], bundle["analysis_date"])
    with pytest.raises(NoMarketDataError, match="stale"):
        vn.indicator(bundle, "rsi", bundle["analysis_date"])
    within = (last + timedelta(days=10)).isoformat()
    assert "Total records:" in vn.prices(bundle, bundle["prices"][0]["date"], within)
    assert numbers(vn.indicator(bundle, "rsi", within, 30))


def test_empty_price_range_routes_to_original_no_data_signal(bundle, monkeypatch):
    monkeypatch.setattr(router, "get_vendor", lambda *a: "vietnam")
    with vietnam.vendor_scope(bundle):
        result = router.route_to_vendor("get_stock_data", "VHM.VN", "2020-01-01", "2020-01-31")
    assert result.startswith("NO_DATA_AVAILABLE:")
    assert "Do not" in result and "fabricate" in result


def test_stale_primary_snapshot_stops_before_fetching_other_sources(tmp_path, monkeypatch):
    calls = []
    def request(_session, url, payload=None):
        calls.append(url)
        return [{"t": [pd.Timestamp("2025-01-01", tz="UTC").timestamp()],
                 "o": [100], "h": [101], "l": [99], "c": [100], "v": [1000]}]
    monkeypatch.setattr(vietnam, "_request", request)
    with pytest.raises(NoMarketDataError, match="stale"):
        vietnam.load("VHM.VN", "2026-10-30", tmp_path, refresh=True)
    assert calls == [vietnam.CHART]
    assert not list(tmp_path.rglob("VHM_*.json"))
