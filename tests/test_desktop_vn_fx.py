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
        "name": "US DOLLAR",
        "cash_buy": 25570,
        "transfer_buy": 25600,
        "sell": 25980,
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
    assert session.calls == 2 and failed["status"] == "cached_source_error"
    assert failed["retrieved_at"] == first["retrieved_at"]
    assert failed["currencies"] == first["currencies"]


def archived_data(day):
    return json.dumps({
        "Count": 2,
        "Date": day + "T00:00:00",
        "UpdatedDate": day + "T23:00:00+07:00",
        "Data": [{
            "currencyCode": "USD", "currencyName": "US DOLLAR",
            "cash": "25300.00", "transfer": "25330.00", "sell": "25710.00",
        }, {
            "currencyCode": "DKK", "currencyName": "DANISH KRONE",
            "cash": "0.00", "transfer": "3742.79", "sell": "3886.09",
        }],
    }).encode()


class ArchiveSession(Session):
    def get(self, url, *, params, **kwargs):
        assert url == f.ARCHIVE_URL
        assert params == {"date": "2026-10-29"}
        response = super().get(url, **kwargs)
        response.url = url
        return response


def test_archive_rejects_quotes_for_wrong_day(tmp_path):
    for raw in (xml(), archived_data("2026-10-30"), archived_data("2026-10-28")):
        session = ArchiveSession(raw=raw)
        result = f.load("2026-10-29", tmp_path, now=NOW, session=session)
        assert session.calls == 1 and result["status"] == "withheld"
        assert all(v["value"] is None for v in f.metrics(result, "2026-10-29").values())


def test_exact_day_archive_preserves_date_source_and_cache(tmp_path):
    session = ArchiveSession(raw=archived_data("2026-10-29"))
    archived = f.load("2026-10-29", tmp_path, now=NOW, session=session)
    assert session.calls == 1
    assert archived["status"] == "available" and archived["source_basis"] == "dated_bank_archive"
    assert archived["quote_time"] == "2026-10-29T23:00:00+07:00"
    assert archived["currencies"]["DKK"]["cash_buy"] is None
    metric = f.metrics(archived, "2026-10-29")["vn_fx_usd_sell"]
    assert metric["value"] == 25710 and metric["source"] == f.ARCHIVE_URL
    tool = json.loads(f.tool(archived, "vn_fx:USD"))
    assert tool["source_url"] == f.ARCHIVE_URL and tool["quotes"]["transfer_buy"] == 25330
    offline = ArchiveSession(error=True)
    assert f.load("2026-10-29", tmp_path, now=NOW, session=offline) == archived
    assert offline.calls == 0


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


class SequenceSession(Session):
    def __init__(self, errors):
        super().__init__()
        self.errors = iter(errors)

    def get(self, *args, **kwargs):
        error = next(self.errors, None)
        if error:
            self.calls += 1
            raise error
        return super().get(*args, **kwargs)


def test_transient_connect_timeout_recovers_without_missing_quotes(tmp_path):
    session = SequenceSession([requests.ConnectTimeout("network timeout")])
    result = f.load("2026-10-30", tmp_path, now=NOW, session=session)
    assert session.calls == 2 and result["status"] == "available"
    assert result["currencies"]["USD"]["sell"] == 25980
    assert result["download_attempts"] == [
        {"attempt": 1, "error": "ConnectTimeout"},
        {"attempt": 2, "status": "received"},
    ]
    assert result["quote_time"] == "2026-10-30T16:39:01+07:00"


def test_persistent_read_timeout_is_bounded_and_disclosed(tmp_path):
    session = SequenceSession([requests.ReadTimeout("timeout")] * 3)
    result = f.load("2026-10-30", tmp_path, now=NOW, session=session)
    assert session.calls == 2 and result["status"] == "unavailable"
    assert result["reason"] == "ReadTimeout" and len(result["download_attempts"]) == 2
    assert all(m["value"] is None for m in f.metrics(result, "2026-10-30").values())


@pytest.mark.parametrize(
    "error",
    [
        requests.HTTPError("403"),
        requests.HTTPError("429"),
        requests.exceptions.SSLError("certificate"),
    ],
)
def test_http_denial_or_tls_failure_is_not_retried(tmp_path, error):
    session = SequenceSession([error])
    result = f.load("2026-10-30", tmp_path, now=NOW, session=session)
    assert session.calls == 1 and result["status"] == "unavailable"
    assert result["reason"] == type(error).__name__
