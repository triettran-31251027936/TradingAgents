"""VCB posted bid/ask and vintage semantics, with no brokerage or model calls."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests

from desktop import fx_vietnam as f

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 30, 17, tzinfo=TZ)


def xml(
    stamp="10/30/2026 4:39:01 PM",
    buy="25,570.00",
    transfer="25,600.00",
    sell="25,980.00",
    duplicate=False,
):
    row = f'<Exrate CurrencyCode="USD" CurrencyName=" US DOLLAR " Buy="{buy}" Transfer="{transfer}" Sell="{sell}"/>'
    return (
        "<ExrateList><DateTime>"
        + stamp
        + "</DateTime>"
        + row
        + (row if duplicate else "")
        + "</ExrateList>"
    ).encode()


class Session:
    def __init__(self, error=False, raw=None):
        self.headers = {}
        self.calls = 0
        self.error = error
        self.raw = raw or xml()

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise requests.ConnectionError("test offline")

        class Response:
            content = self.raw

            def raise_for_status(self):
                pass

        return Response()


def test_three_distinct_quotes_and_bank_clock():
    result = f.parse(xml(), NOW)
    assert result["currencies"]["USD"] == {
        "name": "US DOLLAR", "cash_buy": 25570, "transfer_buy": 25600, "sell": 25980
    }
    assert result["quote_time"] == "2026-10-30T16:39:01+07:00"


@pytest.mark.parametrize(
    "raw",
    [
        xml(buy="26,000.00"),
        xml(transfer="-100"),
        xml(buy="25.570,00"),
        xml(duplicate=True),
        xml(stamp="10/31/2026 4:00:00 PM"),
        b"<html/>",
    ],
)
def test_future_invalid_crossed_or_ambiguous_quotes_rejected(raw):
    with pytest.raises(ValueError):
        f.parse(raw, NOW)


def test_dash_missing_cash_buy_is_not_zero():
    result = f.parse(xml(buy="-"), NOW)
    assert result["currencies"]["USD"]["cash_buy"] is None
    assert result["currencies"]["USD"]["transfer_buy"] == 25600


def test_public_feed_cache_and_failed_refresh_retain_timestamp(tmp_path):
    first = f.load("2026-10-30", tmp_path, now=NOW, session=Session())
    session = Session(error=True)
    cached = f.load("2026-10-30", tmp_path, now=NOW + timedelta(minutes=10), session=session)
    assert session.calls == 0 and cached["quote_time"] == first["quote_time"]
    failed = f.load("2026-10-30", tmp_path, now=NOW + timedelta(hours=1), session=session)
    assert session.calls == 1 and failed["status"] == "cached_source_error"
    assert failed["retrieved_at"] == first["retrieved_at"]
    assert failed["currencies"] == first["currencies"]


def test_current_quote_not_used_for_unobserved_history(tmp_path):
    session = Session()
    result = f.load("2026-10-29", tmp_path, now=NOW, session=session)
    assert session.calls == 0 and result["status"] == "withheld"
    assert all(value["value"] is None for value in f.metrics(result, "2026-10-29").values())


def test_archive_can_replay_observed_quote_without_new_download(tmp_path):
    f.load(
        "2026-10-29",
        tmp_path,
        now=NOW - timedelta(days=1),
        session=Session(raw=xml(stamp="10/29/2026 4:39:01 PM")),
    )
    session = Session(error=True)
    old = f.load("2026-10-29", tmp_path, now=NOW, session=session)
    assert session.calls == 0 and old["quote_time"].startswith("2026-10-29")


def test_metrics_and_original_macro_tool_distinguish_retail_from_sbv(tmp_path):
    from desktop.macro_vietnam import tool

    result = f.load("2026-10-30", tmp_path, now=NOW, session=Session())
    metrics = f.metrics(result, "2026-10-30")
    assert metrics["vn_fx_usd_sell"]["unit"] == "VND/USD"
    assert metrics["vn_fx_usd_sell"]["value"] == 25980
    response = json.loads(
        tool({"asof": "2026-10-30", "bank_fx": result}, "vn_fx_usd_sell", "2026-10-30")
    )
    assert response["value"] == 25980 and "Not SBV" in response["scope"]
    assert (
        json.loads(tool({"asof": "2026-10-30", "bank_fx": result}, "vn_fx:USD", "2026-10-30"))[
            "quotes"
        ]["cash_buy"]
        == 25570
    )
