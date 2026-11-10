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


@pytest.mark.parametrize("prices_only,expected_count", [(False, 1500), (True, 600)])
def test_stale_primary_snapshot_stops_before_fetching_other_sources(tmp_path, monkeypatch, prices_only, expected_count):
    calls = []
    def request(_session, url, payload=None):
        calls.append(url)
        assert payload["countBack"] == expected_count
        return [{"t": [pd.Timestamp("2025-01-01", tz="UTC").timestamp()],
                 "o": [100], "h": [101], "l": [99], "c": [100], "v": [1000]}]
    monkeypatch.setattr(vietnam, "_request", request)
    with pytest.raises(NoMarketDataError, match="stale"):
        vietnam.load("VHM.VN", "2026-10-30", tmp_path, refresh=True, prices_only=prices_only)
    assert calls == [vietnam.CHART]
    assert not list(tmp_path.rglob("VHM_*.json"))


def test_five_year_analysis_history_keeps_leap_year_boundary_and_execution_rows():
    rows = [{"date": d} for d in ["2019-02-27", "2019-02-28", "2019-03-01", "2024-02-29"]]
    assert [r["date"] for r in vietnam.analysis_history(rows, "2024-02-29")] == [
        "2019-02-28", "2019-03-01", "2024-02-29"
    ]
    assert vietnam.analysis_history(rows, "2024-02-29", prices_only=True) is rows


def test_bad_candle_outside_requested_history_does_not_poison_current_window():
    raw = [{"t": [pd.Timestamp(d, tz="UTC").timestamp() for d in ["2021-08-23", "2026-11-03"]],
            "o": [1329.43, 100], "h": [1326.07, 101], "l": [1298.86, 99],
            "c": [1298.86, 100], "v": [1000, 1000]}]
    rows = vietnam.normalize_prices(raw, "2026-11-04", start="2021-11-04")
    assert [r["date"] for r in rows] == ["2026-11-03"]
    with pytest.raises(ValueError, match="high/low"):
        vietnam.normalize_prices(raw, "2026-11-04", start="2021-01-01")


def test_invalid_normalization_window_rejected():
    with pytest.raises(ValueError, match="start"):
        vietnam.normalize_prices({}, "2026-11-04", start="2026-11-05")


def test_historical_price_anchor_restores_absolute_scale_without_changing_returns():
    from desktop.technical_vietnam import anchor_history

    rows = [
        {"date": "2026-08-04", "open": 74100, "high": 76650, "low": 73400, "close": 76450, "volume": 9141600},
        {"date": "2026-08-05", "open": 77100, "high": 79400, "low": 76500, "close": 76500, "volume": 10722235},
    ]
    item = {
        "Symbol": "VHM", "Ngay": "05/08/2026", "GiaMoCua": 154.2, "GiaCaoNhat": 158.8,
        "GiaThapNhat": 153, "GiaDongCua": 153, "GiaDieuChinh": 76.5,
    }

    def fetch(_name, url):
        from urllib.parse import parse_qs, urlsplit

        params = parse_qs(urlsplit(url).query)
        assert params["StartDate"] == params["EndDate"] == ["08/05/2026"]
        assert params["Symbol"] == ["VHM"]
        return {"Success": True, "Data": {"TotalCount": 1, "Data": [item]}}

    scaled, basis = anchor_history(rows, "VHM", fetch)
    assert scaled[-1]["close"] == 153000
    assert scaled[-1]["volume"] == rows[-1]["volume"]
    assert scaled[-1]["close"] / scaled[0]["close"] == pytest.approx(
        rows[-1]["close"] / rows[0]["close"]
    )
    assert basis["scale_factor"] == 2
    assert "Earlier bars remain adjusted" in basis["description"]
    assert rows[-1]["close"] == 76500

    for field, incorrect in [("Ngay", "04/08/2026"), ("GiaDieuChinh", 77), ("GiaMoCua", 157)]:
        original = item[field]
        item[field] = incorrect
        original_rows, unavailable = anchor_history(rows, "VHM", fetch)
        assert original_rows is rows
        assert unavailable["status"] == "unverified"
        item[field] = original
