import pytest

from desktop.account_metrics import evidence
from desktop.odd_lot_quotes import board_row, decode


FRAME = "N4Igtgzg5gKgngBwKYgFwBYA04kQgQyhVRADUAJAWQB90B2AKnIGUANagNg4Y4CYA6AKycAnAwDM1OgAYG9aSKnc+QqY3TTqADl48BHbeIYBGEdIAkIAL5A="


def test_vhm_odd_quote_decodes_exact_prices_and_keeps_round_lot_guard():
    payload = decode(FRAME)
    quote = board_row(payload["message"], "VHM")
    assert quote["best_ask_vnd"] == 62_600
    assert quote["best_ask_shares"] == 190
    assert quote["last_match_vnd"] == 62_500
    with pytest.raises(ValueError):
        board_row(payload["message"], "VIC")
    with pytest.raises(ValueError):
        decode(FRAME[:10])
    q = {"ticker": "VHM.VN", "prices": [{"date": "2026-11-11"}],
         "metrics": {"close": {"value": 62500}}}
    book = {"mode": "local_backtrader_live_minutes", "clock": "2026-11-12",
            "fingerprint": "book", "nav": 3_000_000, "available_cash": 3_000_000,
            "positions": {}, "max_position_fraction": 1, "commission": 0.0015,
            "odd_lot_quote": {**quote, "status": "observed", "observed_at": "2026-11-12T11:00:00+07:00"}}
    sizing = evidence(q, book)["metrics"]
    assert sizing["max_buy_quantity_at_reference"]["value"] == 0
    assert sizing["max_odd_lot_shares_at_observed_ask"]["value"] == 47


def test_odd_lot_tape_withholds_aggregated_stale_and_reset_updates():
    from datetime import datetime

    from desktop.odd_lot_quotes import OddLotTape, TZ

    tape = OddLotTape("VIC", 6752)
    instant = datetime(2026, 11, 12, 11, tzinfo=TZ)
    stale = instant.replace(minute=10)
    tape.update("VIC|66*242.3|69*1|70*6753$", instant, instant)
    tape.update("VIC|69*7|70*6763$", instant, instant)
    tape.update("VIC|66*242.3|69*10|70*6773$", instant, stale)
    tape.update("VIC|66*242.3|69*1|70*6774$", instant, instant)
    tape.update("VIC|66*242.3|69*1|70*6774$", instant, instant)
    tape.update("VIC|70*6700$", instant, instant)
    tape.update("VIC|66*242.3|69*1|70*6701$", instant, instant)
    assert [(r["price_vnd"], r["shares"]) for r in tape.verified] == [(242300, 1), (242300, 1)]
    assert tape.ambiguous == 3 and tape.invalidated
