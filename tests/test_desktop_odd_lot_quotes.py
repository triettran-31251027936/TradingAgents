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
