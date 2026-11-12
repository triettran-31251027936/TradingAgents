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


def proposal(store, ticker="HPG.VN"):
    today = datetime.now(TZ).date().isoformat()
    bundle = {
        "ticker": ticker,
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


@pytest.mark.parametrize("exchange", ["HOSE", "HNX", "UPCOM"])
def test_published_holidays_and_working_saturdays_are_closed(exchange):
    for day in ("2025-05-02", "2026-01-02", "2026-02-18", "2026-08-22", "2026-08-31"):
        assert phase(datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ), exchange) == "closed"
    assert phase(datetime(2026, 2, 23, 10, tzinfo=TZ), exchange) == "continuous"


def test_lunar_holiday_is_not_a_history_gap_for_an_approved_order(tmp_path, monkeypatch):
    from desktop import paper

    approved = datetime(2026, 2, 13, 14, 46, tzinfo=TZ)
    monkeypatch.setattr(paper, "_utc", lambda: approved.isoformat())
    store = PracticeStore(tmp_path)
    p = proposal(store, "BSR.VN")
    sign(store, p, monkeypatch)
    first = datetime(2026, 2, 23, 9, 15, tzinfo=TZ)
    holiday = datetime(2026, 2, 16, 9, 15, tzinfo=TZ)
    rows = [
        {**bar("09:15:00"), "time": int(value.timestamp())}
        for value in (holiday, first, first.replace(minute=16))
    ]
    source = {**board(rows), "completed_sessions": ["2026-02-16", "2026-02-23"]}
    account = store.sync({"BSR.VN": source}, now=first.replace(minute=17))
    assert account["orders"][0]["status"] == "FILLED"
    assert account["orders"][0]["result"]["fill_date"] == "2026-02-23"
    assert account["positions"]["BSR.VN"]["available_quantity"] == 0
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0] == 2


def test_settlement_ignores_reported_session_on_published_market_holiday(tmp_path, monkeypatch):
    from desktop import paper

    approved = datetime(2026, 4, 29, 9, 16, tzinfo=TZ)
    monkeypatch.setattr(paper, "_utc", lambda: approved.isoformat())
    store = PracticeStore(tmp_path)
    p = proposal(store, "BSR.VN")
    sign(store, p, monkeypatch)
    rows = [
        {**bar("09:15:00"), "time": int(approved.replace(minute=minute).timestamp())}
        for minute in range(15, 19)
    ]
    source = {**board(rows), "completed_sessions": ["2026-04-29", "2026-04-30", "2026-05-01"]}
    waiting = store.sync({"BSR.VN": source}, now=datetime(2026, 5, 1, 15, tzinfo=TZ))
    assert waiting["orders"][0]["status"] == "FILLED"
    assert waiting["positions"]["BSR.VN"]["available_quantity"] == 0
    source["completed_sessions"] += ["2026-05-04", "2026-05-05"]
    settled = store.sync({"BSR.VN": source}, now=datetime(2026, 5, 5, 15, tzinfo=TZ))
    assert settled["positions"]["BSR.VN"]["available_quantity"] == 100


def test_published_hnx_transition_closes_only_the_old_listed_venue():
    for day in (24, 25):
        stamp = datetime(2026, 12, day, 10, tzinfo=TZ)
        assert phase(stamp, "HNX") == "closed"
        assert phase(stamp, "HOSE") == phase(stamp, "UPCOM") == "continuous"
    first_hose = datetime(2026, 12, 28, 9, 16, tzinfo=TZ)
    assert phase(first_hose, "HNX") == "closed"
    assert phase(first_hose, "HOSE") == "continuous"


def test_hnx_fill_replays_original_hours_after_market_board_moves_to_hose():
    old_session = datetime(2026, 12, 23, 9, tzinfo=TZ)
    new_session = datetime(2026, 12, 28, 9, 15, tzinfo=TZ)
    rows = [
        {**bar("09:15:00"), "time": int(value.timestamp())}
        for value in (old_session, old_session.replace(minute=1), new_session)
    ]
    hnx_order = {
        **order(), "ticker": "PVS.VN", "exchange": "HNX",
        "approved_at": old_session.isoformat(),
    }
    result = run_broker(
        [hnx_order], {"PVS.VN": rows}, new_session.replace(minute=16), {"PVS.VN": "HOSE"}
    )
    fill = result["orders"][hnx_order["id"]]
    assert fill["state"] == "FILLED" and fill["fill_date"] == "2026-12-23"


def test_unmatched_hnx_order_expires_and_requires_new_hose_order():
    after_last_hnx = datetime(2026, 12, 23, 14, 46, tzinfo=TZ)
    first_hose = datetime(2026, 12, 28, 9, 15, tzinfo=TZ)
    rows = [
        {**bar("09:15:00"), "time": int(first_hose.replace(minute=m).timestamp())}
        for m in (15, 16)
    ]
    old_order = {
        **order(), "ticker": "PVS.VN", "exchange": "HNX",
        "approved_at": after_last_hnx.isoformat(),
    }
    new_order = {
        **old_order, "id": "new-approval", "exchange": "HOSE",
        "approved_at": first_hose.isoformat(),
    }
    result = run_broker(
        [old_order, new_order],
        {"PVS.VN": rows},
        first_hose.replace(minute=17),
        {"PVS.VN": "HOSE"},
    )
    assert result["orders"][old_order["id"]]["state"] == "EXPIRED"
    assert result["orders"][old_order["id"]]["filled_quantity"] == 0
    assert result["orders"][new_order["id"]]["state"] == "FILLED"
    assert result["orders"][new_order["id"]]["fill_date"] == "2026-12-28"
    assert result["positions"]["PVS.VN"]["quantity"] == 100


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


def test_closed_order_does_not_block_marks_after_an_unobserved_week(tmp_path, monkeypatch):
    store, _ = approved_store(tmp_path, monkeypatch)
    rows = [bar(f"09:{m}:00") for m in range(15, 20)]
    initial = store.sync({"HPG.VN": board(rows)}, now=moment("09:20:00"))
    assert initial["orders"][0]["status"] == "FILLED"
    later_time = datetime(2026, 11, 10, 9, 19, tzinfo=TZ)
    later_bar = {**bar("09:19:00", price=25500), "time": int(later_time.timestamp())}
    later_board = {**board([later_bar]), "completed_sessions": ["2026-11-10"]}
    updated = store.sync({"HPG.VN": later_board}, now=later_time.replace(minute=20))
    assert updated["orders"] == initial["orders"]
    assert updated["cash"] == initial["cash"]
    assert updated["positions"]["HPG.VN"]["mark_price"] == 25500


def test_late_bars_cannot_reopen_expired_order(tmp_path, monkeypatch):
    store, _ = approved_store(tmp_path, monkeypatch)
    rows = [bar(f"09:{m}:00", price=26000) for m in range(15, 20)]
    expired = store.sync({"HPG.VN": board(rows)}, now=moment("15:00:00"))
    assert expired["orders"][0]["status"] == "EXPIRED"

    late = board([bar("09:20:00", price=24000)])
    with pytest.raises(ValueError, match="lệnh đã kết thúc thay đổi"):
        store.sync({"HPG.VN": late}, now=moment("15:01:00"))
    unchanged = store.snapshot()
    assert unchanged["orders"] == expired["orders"]
    assert unchanged["cash"] == 3_000_000 and not unchanged["positions"]


def test_market_dispatch_only_fetches_selected_open_or_held_tickers(tmp_path, monkeypatch):
    from desktop import bridge, market_board

    store, _ = approved_store(tmp_path, monkeypatch)
    rows = [bar(f"09:{m}:00", price=26000) for m in range(15, 20)]
    expired = store.sync({"HPG.VN": board(rows)}, now=moment("15:00:00"))
    assert expired["orders"][0]["status"] == "EXPIRED"
    monkeypatch.setattr(
        bridge,
        "config",
        lambda: {"data_cache_dir": str(tmp_path), "desktop_vietnam": {"paper_enabled": True}},
    )

    def fetch(ticker, interval, cache_dir):
        assert ticker == "BSR.VN"
        return board(rows)

    monkeypatch.setattr(market_board, "board", fetch)
    report = bridge.dispatch("paper_market", {"ticker": "BSR.VN", "interval": "1m"})
    assert report["matching_error"] is None
    assert report["account"]["orders"] == expired["orders"]


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
    def daily(*args, **kwargs):
        assert kwargs == {"prices_only": True, "price_history_limit": 60}
        return {"exchange": "HOSE", "prices": prices}

    monkeypatch.setattr(market_board, "load", daily)
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


def test_board_with_old_hnx_identity_does_not_show_cutover_candles(tmp_path, monkeypatch):
    from desktop import market_board

    before = datetime(2026, 12, 23, 14, 28, tzinfo=TZ)
    after = datetime(2026, 12, 28, 10, tzinfo=TZ)
    times = [int(value.timestamp()) for value in (before, after)]
    raw = {
        "t": times,
        "o": [25000, 26000],
        "h": [25100, 26100],
        "l": [24900, 25900],
        "c": [25000, 26000],
        "v": [10000, 10000],
    }

    def request(session, url, payload):
        if url == market_board.CHART:
            return raw
        return {"data": [{"id": "cutover", "truncTime": times[-1], "matchPrice": 26000, "matchVol": 100}]}

    monkeypatch.setattr(market_board, "_request", request)
    monkeypatch.setattr(
        market_board, "load", lambda *a, **kw: {
            "exchange": "HNX", "prices": [{"date": "2026-12-23", "close": 25000}],
        }
    )
    data = market_board.board("PVS.VN", "1m", tmp_path, now=after)
    assert data["status"] == "closed" and not data["trades"]
    assert data["quote"]["price"] == 25000
    assert [row["time"] for row in data["candles"]] == [times[0]]
    assert data["calendar"]["hnx_listed_transition"]["scheduled_hose_start"] == "2026-12-28"


def test_bsr_buy_sell_round_trip_requires_separate_approvals_and_t2(tmp_path, monkeypatch):
    from desktop import paper, practice

    clock = [moment("09:16:30")]

    class TradeClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(practice, "datetime", TradeClock)
    monkeypatch.setattr(paper, "_utc", lambda: clock[0].isoformat())
    store = PracticeStore(tmp_path)
    ticker = "BSR.VN"

    def propose(side, price):
        day = clock[0].date().isoformat()
        return store.proposal(
            side,
            {"execution": {"action": side, "quantity": 100, "limit_price": price,
                           "rationale": "Isolated round trip", "data_gaps": []}},
            {"ticker": ticker, "analysis_date": day, "fingerprint": "isolated",
             "prices": [{"date": day, "close": price}],
             "metrics": {k: {"value": 1} for k in (
                 "close", "adv20", "volatility20_annual", "historical_var95_1d"
             )}},
            "HOSE",
        )

    def session_board(price, sessions):
        rows = [
            {**bar(f"09:{minute}:00", price), "time": int(
                clock[0].replace(hour=9, minute=minute, second=0).timestamp()
            )}
            for minute in range(15, 20)
        ]
        return {**board(rows), "completed_sessions": sessions}

    purchase = propose("Buy", 25000)
    with pytest.raises(ValueError, match="xác nhận"):
        store.approve(purchase["id"], purchase["preview_hash"], {})
    assert sign(store, purchase, monkeypatch)["status"] == "APPROVED"
    bought = store.sync({ticker: session_board(25000, [DAY])}, now=moment("09:20:00"))
    assert bought["orders"][0]["status"] == "FILLED"
    assert bought["positions"][ticker]["available_quantity"] == 0
    with pytest.raises(ValueError, match="Không đủ cổ phiếu đã về tài khoản"):
        propose("Sell", 26000)

    first_settlement = ["2026-10-30", "2026-11-02", "2026-11-03"]
    settled_buy = store.sync(
        {ticker: session_board(25000, first_settlement)},
        now=datetime(2026, 11, 3, 15, tzinfo=TZ),
    )
    assert settled_buy["positions"][ticker]["available_quantity"] == 100

    clock[0] = datetime(2026, 11, 4, 9, 16, 30, tzinfo=TZ)
    sale = propose("Sell", 26000)
    with pytest.raises(ValueError):
        store.approve(sale["id"], sale["preview_hash"], {})
    marked = store.sync(
        {ticker: session_board(26000, first_settlement)},
        now=clock[0].replace(minute=16, second=30),
    )
    assert marked["positions"][ticker]["quantity"] == 100
    assert marked["orders"][1]["status"] == "PROPOSED"
    assert sign(store, store.preview(sale["id"]), monkeypatch)["status"] == "APPROVED"
    sold = store.sync(
        {ticker: session_board(26000, first_settlement)},
        now=clock[0].replace(minute=20, second=0),
    )
    assert sold["orders"][1]["status"] == "FILLED"
    assert sold["orders"][1]["result"]["fee"] == pytest.approx(6500)
    assert sold["positions"] == {}
    assert sold["cash"] == pytest.approx(3089750)
    assert sold["available_cash"] == pytest.approx(496250)
    settled_sale = store.sync(
        {ticker: session_board(26000, first_settlement + ["2026-11-04", "2026-11-05", "2026-11-06"])},
        now=datetime(2026, 11, 6, 15, tzinfo=TZ),
    )
    assert settled_sale["available_cash"] == pytest.approx(3089750)


def test_stale_unapproved_proposal_expires_without_market_sync(tmp_path, monkeypatch):
    from datetime import timedelta

    import desktop.practice as practice

    current = datetime.now(TZ)
    store = PracticeStore(tmp_path)
    preview = proposal(store, "BSR.VN")
    assert store.snapshot()["orders"][0]["status"] == "PROPOSED"

    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return (current + timedelta(days=1)).astimezone(tz)

    monkeypatch.setattr(practice, "datetime", Tomorrow)
    order_id = preview["id"]
    expired = store.preview(order_id)
    assert expired["status"] == "EXPIRED"
    assert expired["preview_hash"] != preview["preview_hash"]
    book = store.snapshot()
    assert book["orders"][0]["status"] == "EXPIRED"
    assert book["orders"][0]["approved_at"] is None
    assert book["available_cash"] == 3_000_000 and not book["positions"]
    with pytest.raises(ValueError, match="sang ngày mới"):
        sign(store, preview, monkeypatch)
    with pytest.raises(ValueError, match="hết hạn"):
        store.reject(order_id)


def test_hose_odd_lot_uses_only_reconciled_post_approval_prints(tmp_path, monkeypatch):
    from desktop import paper, practice

    clock = [moment("09:16:30")]

    class TradeClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(practice, "datetime", TradeClock)
    monkeypatch.setattr(paper, "_utc", lambda: clock[0].isoformat())
    store = PracticeStore(tmp_path)
    ticker = "VHM.VN"
    bundle = {
        "ticker": ticker, "analysis_date": DAY, "fingerprint": "isolated",
        "prices": [{"date": DAY, "close": 62500}],
        "odd_lot_quote": {"status": "observed", "ticker": ticker, "observed_at": clock[0].isoformat()},
        "metrics": {key: {"value": 1} for key in (
            "close", "adv20", "volatility20_annual", "historical_var95_1d"
        )},
    }
    decision = {"execution": {"action": "Buy", "quantity": 3, "limit_price": 62600,
                             "rationale": "Isolated VHM", "data_gaps": []}}
    preview = store.proposal("test", decision, bundle, "HOSE")
    assert preview["order"]["lot"] == "odd"
    assert sign(store, preview, monkeypatch)["status"] == "APPROVED"
    candles = {ticker: board([bar(f"09:{minute}:00", 62000) for minute in range(15, 19)])}
    without_odd = store.sync(candles, now=moment("09:19:00"))
    assert without_odd["orders"][0]["result"]["filled_quantity"] == 0

    def event(clock, previous, quantity, price=62500):
        stamp = moment(clock).isoformat()
        return {"observed_at": stamp, "board_server_clock": stamp, "price_vnd": price,
                "shares": quantity, "cumulative_shares": previous + quantity,
                "previous_cumulative_shares": previous}

    quote = {"ticker": ticker, "status": "observed", "sample": {"reconciled_updates": [
        event("09:16:20", 90, 3), event("09:17:14", 93, 1), event("09:17:16", 94, 2),
    ]}}
    result = store.sync(candles, now=moment("09:19:00"), odd_quotes={ticker: quote})
    receipt = result["orders"][0]["result"]
    assert receipt["state"] == "FILLED" and receipt["filled_quantity"] == 3
    assert receipt["fill_price"] == 62500 and receipt["fee"] == pytest.approx(281.25)
    assert result["cash"] == pytest.approx(2_812_218.75)
    assert result["positions"][ticker]["available_quantity"] == 0
    retried = store.sync(candles, now=moment("09:19:01"), odd_quotes={ticker: quote})
    assert retried["orders"][0]["result"] == receipt
    settled = store.sync(
        {ticker: {**candles[ticker], "completed_sessions": [DAY, "2026-11-02", "2026-11-03"]}},
        now=datetime(2026, 11, 3, 15, tzinfo=TZ),
    )
    assert settled["positions"][ticker]["available_quantity"] == 3


def test_background_tick_only_requests_market_data_for_approved_orders(tmp_path, monkeypatch):
    from desktop import bridge, market_board

    monkeypatch.setattr(
        bridge, "config",
        lambda: {"data_cache_dir": str(tmp_path), "desktop_vietnam": {"paper_enabled": True}},
    )
    store = PracticeStore(tmp_path)
    p = proposal(store, "VHM.VN")
    requested = []

    def fetch(ticker, interval, cache_dir):
        requested.append((ticker, interval))
        raise RuntimeError("temporary feed outage")

    monkeypatch.setattr(market_board, "board", fetch)
    assert bridge.dispatch("paper_tick", {}) == {"pending": False, "matching_error": None}
    assert requested == []
    sign(store, p, monkeypatch)
    result = bridge.dispatch("paper_tick", {})
    assert requested == [("VHM.VN", "1m")]
    assert result["pending"] is True
    assert store.snapshot()["orders"][0]["status"] == "APPROVED"
