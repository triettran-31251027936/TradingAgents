"""Isolated accounts only: actual BackBroker fills, no network or paid inference."""

import hashlib
import hmac
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from desktop.market_board import normalize, normalize_trades, phase
from desktop.paper import Store
from desktop.practice import PracticeStore, run_broker

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DAY = "2026-10-30"


def moment(clock):
    return datetime.fromisoformat(DAY + "T" + clock).replace(tzinfo=TZ)


def bar(clock, price=25000, volume=100000):
    return {
        "time": int(moment(clock).timestamp()),
        "open": price,
        "high": price + 100,
        "low": price - 100,
        "close": price,
        "volume": volume,
        "complete": True,
    }


def order(approved="09:16:30"):
    return {
        "id": "test",
        "ticker": "HPG.VN",
        "exchange": "HOSE",
        "side": "Buy",
        "quantity": 100,
        "limit_price": 25100,
        "approved_at": moment(approved).isoformat(),
    }


def board(rows):
    return {
        "status": "recent",
        "candles": rows,
        "exchange": "HOSE",
        "completed_sessions": [DAY],
        "quote": {"price": rows[-1]["close"], "time": rows[-1]["time"]},
    }


def proposal(store):
    today = datetime.now(TZ).date().isoformat()
    bundle = {
        "ticker": "HPG.VN",
        "analysis_date": today,
        "fingerprint": "fixture",
        "prices": [{"date": today, "close": 25000}],
        "metrics": {
            k: {"value": 1}
            for k in ["close", "adv20", "volatility20_annual", "historical_var95_1d"]
        },
    }
    decision = {
        "execution": {
            "action": "Buy",
            "quantity": 100,
            "limit_price": 25000,
            "rationale": "Isolated test",
            "data_gaps": [],
        }
    }
    return store.proposal("test", decision, bundle, "HOSE")


def sign(store, p, monkeypatch):
    secret = "isolated-test"
    monkeypatch.setenv("TRADINGAGENTS_PAPER_APPROVAL_KEY", secret)
    timestamp = 123456
    signature = hmac.new(
        secret.encode(), f"{p['id']}|{p['preview_hash']}|{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return store.approve(
        p["id"], p["preview_hash"], {"timestamp": timestamp, "signature": signature}, now=timestamp
    )


def test_exact_3m_account_does_not_reset_legacy(tmp_path):
    old = Store(tmp_path)
    new = PracticeStore(tmp_path)
    assert old.snapshot()["cash"] == 100_000_000
    assert new.snapshot()["cash"] == new.snapshot()["initial_cash"] == 3_000_000
    assert new.snapshot()["max_position_fraction"] == 1
    assert not new.snapshot()["allow_margin"]


def test_proposal_cannot_spend_until_native_consent(tmp_path, monkeypatch):
    s = PracticeStore(tmp_path)
    p = proposal(s)
    assert p["status"] == "PROPOSED"
    assert s.snapshot()["cash"] == 3_000_000 and not s.snapshot()["positions"]
    with pytest.raises(ValueError, match="xác nhận"):
        s.approve(p["id"], p["preview_hash"], {})
    sign(s, p, monkeypatch)
    assert s.snapshot()["available_cash"] == 496250
    assert not s.snapshot()["positions"]
    with pytest.raises(ValueError):
        sign(s, p, monkeypatch)


def test_no_preapproval_fills_actual_next_completed_minute():
    rows = [bar(f"09:{m}:00") for m in range(15, 20)]
    result = run_broker([order()], {"HPG.VN": rows}, moment("09:20:00"), {"HPG.VN": "HOSE"})
    fill = result["orders"]["test"]
    assert fill["state"] == "FILLED"
    assert fill["fill_time"] == moment("09:18:00").isoformat()
    assert fill["filled_quantity"] == 100
    assert result["cash"] == 496250


def test_approval_after_last_bar_does_not_fill():
    result = run_broker(
        [order("10:00:00")],
        {"HPG.VN": [bar("09:15:00"), bar("09:16:00")]},
        moment("10:01:00"),
        {"HPG.VN": "HOSE"},
    )
    assert result["orders"]["test"]["filled_quantity"] == 0
    assert result["cash"] == 3_000_000


def test_low_volume_no_fractional_or_fake_lot():
    rows = [bar(f"09:{m}:00", volume=9000) for m in range(15, 20)]
    result = run_broker([order()], {"HPG.VN": rows}, moment("09:20:00"), {"HPG.VN": "HOSE"})
    assert result["orders"]["test"]["filled_quantity"] == 0


def test_limit_not_crossed_expires():
    rows = [bar(f"09:{m}:00", price=26000) for m in range(15, 20)]
    result = run_broker([order()], {"HPG.VN": rows}, moment("15:00:00"), {"HPG.VN": "HOSE"})
    assert result["orders"]["test"]["state"] == "EXPIRED"
    assert result["cash"] == 3_000_000


def test_auction_does_not_fill():
    rows = [
        bar("14:28:00", price=26000),
        bar("14:29:00", price=26000),
        bar("14:30:00"),
        bar("14:44:00"),
    ]
    result = run_broker(
        [order("14:28:30")], {"HPG.VN": rows}, moment("15:00:00"), {"HPG.VN": "HOSE"}
    )
    assert result["orders"]["test"]["filled_quantity"] == 0


def test_sync_unapproved_never_fills_and_preserves_real_bar(tmp_path):
    s = PracticeStore(tmp_path)
    proposal(s)
    rows = [bar("09:15:00"), bar("09:16:00")]
    b = s.sync({"HPG.VN": board(rows)}, now=moment("09:20:00"))
    assert b["cash"] == 3_000_000 and not b["positions"]
    revision = [{**rows[0], "volume": rows[0]["volume"] + 1}, rows[1]]
    with pytest.raises(ValueError, match="thay đổi"):
        s.sync({"HPG.VN": board(revision)}, now=moment("09:21:00"))
    assert s.snapshot()["cash"] == 3_000_000


def test_future_and_unclosed_candles_not_consumed(tmp_path):
    s = PracticeStore(tmp_path)
    rows = [bar("09:15:00"), bar("10:00:00")]
    s.sync({"HPG.VN": board(rows)}, now=moment("09:16:00"))
    with s.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0] == 1


def test_cannot_daily_replay_into_live_account(tmp_path):
    with pytest.raises(ValueError):
        PracticeStore(tmp_path).advance(DAY, {})


def test_normalize_does_not_fabricate_gaps_and_skips_future():
    rows = [bar("09:15:00"), bar("09:18:00"), bar("10:00:00")]
    raw = [
        dict(
            zip(
                ["t", "o", "h", "l", "c", "v"],
                [[r[k] for r in rows] for k in ["time", "open", "high", "low", "close", "volume"]],
                strict=True,
            )
        )
    ]
    result = normalize(raw, "1m", moment("09:18:30"))
    assert len(result) == 2 and result[0]["complete"] and not result[1]["complete"]
    raw[0]["t"][1] = raw[0]["t"][0]
    with pytest.raises(ValueError, match="trùng"):
        normalize(raw, "1m", moment("10:01:00"))


def test_invalid_high_low_rejected():
    raw = {
        "t": [int(moment("09:15:00").timestamp())],
        "o": [25000],
        "h": [24000],
        "l": [24000],
        "c": [25000],
        "v": [100],
    }
    with pytest.raises(ValueError, match="nhất quán"):
        normalize(raw, "1m", moment("10:00:00"))


def test_trade_tape_validates_timestamps_and_duplicates():
    row = {
        "id": "1",
        "truncTime": moment("09:15:00").timestamp(),
        "matchPrice": "25000",
        "matchVol": "100",
        "matchType": "b",
    }
    assert (
        len(
            normalize_trades(
                [row, row, {**row, "id": "2", "matchPrice": "nan"}], moment("10:00:00")
            )
        )
        == 1
    )


def test_market_hours():
    assert phase(moment("11:45:00")) == "lunch"
    assert phase(moment("14:35:00")) == "auction"
    assert phase(moment("14:35:00"), "UPCOM") == "continuous"
    assert phase(moment("09:10:00")) == "auction"


def approved_store(tmp_path, monkeypatch):
    from desktop import paper

    monkeypatch.setattr(paper, "_utc", lambda: moment("09:16:30").isoformat())
    s = PracticeStore(tmp_path)
    p = proposal(s)
    sign(s, p, monkeypatch)
    return s, p


def test_sync_real_broker_fill_live_mark_and_t2(tmp_path, monkeypatch):
    s, p = approved_store(tmp_path, monkeypatch)
    rows = [bar(f"09:{m}:00") for m in range(15, 20)]
    m = board(rows)
    m["quote"]["price"] = 25200
    b = s.sync({"HPG.VN": m}, now=moment("09:20:00"))
    assert b["orders"][0]["status"] == "FILLED"
    assert b["cash"] == 496250 and b["nav"] == 3016250
    assert b["positions"]["HPG.VN"]["available_quantity"] == 0
    next_market = {
        **m,
        "completed_sessions": [DAY, "2026-11-02", "2026-11-03"],
        "quote": {"price": 25300, "time": int(moment("09:20:00").timestamp())},
    }
    settled = s.sync({"HPG.VN": next_market}, now=datetime(2026, 11, 3, 15, tzinfo=TZ))
    assert settled["positions"]["HPG.VN"]["available_quantity"] == 100
    assert settled["nav"] == 3026250


def test_pending_reservation_prevents_exceeding_3m(tmp_path, monkeypatch):
    s, p = approved_store(tmp_path, monkeypatch)
    other = proposal(s)  # Same report id is idempotent.
    assert other["id"] == p["id"]
    o = order()
    o["id"] = "overspend"
    from desktop.paper import validate_order

    with pytest.raises(ValueError):
        validate_order(o, s.snapshot())


def test_offline_history_gap_blocks_matching(tmp_path, monkeypatch):
    s, p = approved_store(tmp_path, monkeypatch)
    rows = [bar("09:15:00"), bar("09:35:00"), bar("09:36:00")]
    with pytest.raises(ValueError, match="Thiếu quá"):
        s.sync({"HPG.VN": board(rows)}, now=moment("09:40:00"))
    assert s.snapshot()["cash"] == 3_000_000 and s.snapshot()["orders"][0]["status"] == "APPROVED"


def test_expiry_without_new_bar_releases_reserved_cash(tmp_path, monkeypatch):
    s, p = approved_store(tmp_path, monkeypatch)
    rows = [bar(f"09:{m}:00", price=26000) for m in range(15, 20)]
    s.sync({"HPG.VN": board(rows)}, now=moment("09:20:00"))
    expired = s.sync({"HPG.VN": board(rows)}, now=moment("15:00:00"))
    assert expired["orders"][0]["status"] == "EXPIRED"
    assert expired["available_cash"] == 3_000_000


def test_market_failure_keeps_original_source_timestamp(tmp_path, monkeypatch):
    import json

    import requests

    from desktop import market_board

    folder = tmp_path / "market-board"
    folder.mkdir()
    cached = {
        "retrieved_epoch": moment("09:15:00").timestamp(),
        "retrieved_at": moment("09:15:00").isoformat(),
        "status": "recent",
        "warnings": [],
    }
    (folder / "HPG-1m.json").write_text(json.dumps(cached))

    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("test")

    monkeypatch.setattr(market_board, "_request", unavailable)
    result = market_board.board("HPG.VN", "1m", tmp_path, now=moment("09:20:00"))
    assert result["status"] == "unavailable"
    assert result["retrieved_at"] == cached["retrieved_at"]


def test_market_failure_without_cache_never_invents_prices(tmp_path, monkeypatch):
    import requests

    from desktop import market_board

    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("test")

    monkeypatch.setattr(market_board, "_request", unavailable)
    with pytest.raises(RuntimeError, match="không tạo dữ liệu giả"):
        market_board.board("HPG.VN", "1m", tmp_path, now=moment("09:20:00"))


@pytest.mark.parametrize(
    "observed", ["2026-10-30T15:00:00", "2026-10-31T09:00:00", "2026-11-02T08:00:00"]
)
@pytest.mark.parametrize("has_previous", [True, False])
def test_market_change_uses_quote_session_before_weekend_or_preopen(
    tmp_path, monkeypatch, observed, has_previous
):
    from desktop import market_board

    quote_time = int(moment("14:45:00").timestamp())
    raw = [
        {
            "t": [quote_time - 60],
            "o": [21000],
            "h": [21000],
            "l": [21000],
            "c": [21000],
            "v": [1000],
        }
    ]

    def request(session, url, payload):
        if url == market_board.CHART:
            return raw
        return {
            "data": [{"id": "last", "truncTime": quote_time, "matchPrice": 21000, "matchVol": 1000}]
        }

    prices = ([{"date": "2026-10-29", "close": 21200}] if has_previous else []) + [
        {"date": DAY, "close": 21000}
    ]
    monkeypatch.setattr(market_board, "_request", request)
    monkeypatch.setattr(
        market_board, "load", lambda *a, **kw: {"exchange": "HOSE", "prices": prices}
    )
    result = market_board.board(
        "HPG.VN", "1m", tmp_path, now=datetime.fromisoformat(observed).replace(tzinfo=TZ)
    )
    if has_previous:
        assert result["reference_date"] == "2026-10-29"
        assert result["change"] == -200
        assert result["change_fraction"] == pytest.approx(21000 / 21200 - 1)
    else:
        assert result["reference_date"] is None
        assert result["change"] is None and result["change_fraction"] is None
