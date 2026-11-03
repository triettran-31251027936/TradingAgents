"""VN settlement uses Vietnam's session day, with no future-dated cache bound."""

from datetime import datetime

import pandas as pd
import pytest

from desktop import vietnam
from tradingagents.memory import settlement


def test_closed_vn_session_is_available_after_local_midnight_before_utc_midnight(
    tmp_path, monkeypatch
):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, 1, 30, tzinfo=vietnam.TZ).astimezone(tz)

    monkeypatch.setattr(vietnam, "datetime", Clock)
    monkeypatch.setattr(settlement, "_now", lambda: pd.Timestamp("2026-11-02T18:30:00Z"))
    calls = []
    days = [
        "2026-10-01",
        "2026-10-23",
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
        "2026-11-02",
        "2026-11-03",
    ]

    def load(ticker, asof, cache_dir, **kwargs):
        calls.append((ticker, asof))
        stock = [99, 99, 100, 101, 102, 103, 104, 105, 9999]
        index = [990, 990, 1000, 1004, 1008, 1012, 1016, 1020, 99999]
        return {
            "prices": [
                {"date": day, "close": value}
                for day, value in zip(days, stock if ticker == "BSR.VN" else index, strict=True)
            ]
        }

    monkeypatch.setattr(vietnam, "load", load)
    with vietnam.settlement_scope(
        {"desktop_vietnam": {"enabled": True}, "data_cache_dir": str(tmp_path)}
    ):
        result = settlement.fetch_returns("BSR.VN", "2026-10-23", 5, benchmark="VNINDEX.VN")
    assert result[:2] == pytest.approx((0.05, 0.03))
    assert result[2:] == (5, "2026-11-02")
    assert calls == [("BSR.VN", "2026-11-03"), ("VNINDEX.VN", "2026-11-03")]


def test_current_local_day_is_still_excluded_by_original_evaluator(tmp_path, monkeypatch):
    monkeypatch.setattr(settlement, "_now", lambda: pd.Timestamp("2026-11-02T16:30:00Z"))
    monkeypatch.setattr(
        vietnam,
        "load",
        lambda *a, **kw: {
            "prices": [
                {"date": "2026-10-30", "close": 100},
                {"date": "2026-11-02", "close": 105},
                {"date": "2026-11-03", "close": 999},
            ]
        },
    )
    with vietnam.settlement_scope(
        {"desktop_vietnam": {"enabled": True}, "data_cache_dir": str(tmp_path)}
    ):
        closes = settlement.get_closes("BSR.VN", "2026-10-30", "2026-11-04")
        assert str(closes.index.tz) == "Asia/Ho_Chi_Minh"
        known = settlement._by_day(closes)
    # 16:30UTC is23:30VN on02/11: that session remains "today" under upstream's
    # conservative rule. Do not use03/11 even if a source supplied a future bar.
    assert known.index.strftime("%Y-%m-%d").tolist() == ["2026-10-30"]
