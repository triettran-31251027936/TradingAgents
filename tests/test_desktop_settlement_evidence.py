"""Original scoring and price receipts agree, including unequal calendars."""

import json

import pandas as pd
import pytest

from desktop import vietnam
from tradingagents.memory import settlement


def quote(days, values):
    return [{"date": day, "close": value} for day, value in zip(days, values, strict=True)]


@pytest.mark.parametrize("pending", [False, True])
def test_receipt_freezes_exact_closes_and_endpoint_calculation(tmp_path, monkeypatch, pending):
    days = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]
    stock = quote(
        days[:3] if pending else days,
        [100, 102, 104] if pending else [100, 102, 104, 103, 106, 110],
    )
    stock.insert(0, {"date": "2026-10-16", "close": 99})
    benchmark = quote(
        ["2026-10-23", "2026-10-27", "2026-10-30", "2026-11-02"], [1000, 1010, 1030, 1040]
    )
    benchmark.insert(0, {"date": "2026-10-16", "close": 990})
    monkeypatch.setattr(settlement, "_now", lambda: pd.Timestamp("2026-11-04T04:00:00Z"))
    monkeypatch.setattr(
        vietnam,
        "load",
        lambda ticker, *a, **kw: {"prices": stock if ticker == "VHM.VN" else benchmark},
    )
    original_fetch, original_closes = settlement.fetch_returns, settlement.get_closes
    cfg = {"data_cache_dir": str(tmp_path), "desktop_vietnam": {"enabled": True}}
    with vietnam.settlement_scope(cfg) as receipts:
        result = settlement.fetch_returns("VHM.VN", "2026-10-23", 5, benchmark="VNINDEX.VN")
    assert settlement.fetch_returns is original_fetch and settlement.get_closes is original_closes
    assert len(receipts) == 1
    path = tmp_path / "settlement-evidence" / receipts[0]["file"]
    proof = json.loads(path.read_text())
    checksum = proof.pop("content_sha256")
    assert checksum == vietnam.digest(proof) == receipts[0]["sha256"]
    assert "not independently verified" in proof["price_basis"]
    assert "account P&L" in proof["price_basis"]
    assert all(call["timezone"] == "Asia/Ho_Chi_Minh" for call in proof["price_reads"])
    if pending:
        assert result == (None, None, None, None) and proof["status"] == "pending"
        assert "calculation" not in proof
    else:
        assert result[0] == pytest.approx(0.10) and result[1] == pytest.approx(0.06)
        calc = proof["calculation"]
        assert calc["entry_date"] == "2026-10-26" and calc["exit_date"] == "2026-11-02"
        assert calc["benchmark_entry_date"] == "2026-10-23"
        assert calc["stock_entry_close"] == 100 and calc["stock_exit_close"] == 110
        assert calc["benchmark_entry_close"] == 1000 and calc["benchmark_exit_close"] == 1040
        assert calc["benchmark_return"] == pytest.approx(0.04)
    # Repeated observations are separate immutable receipts, never an overwrite.
    raw = path.read_bytes()
    with vietnam.settlement_scope(cfg):
        settlement.fetch_returns("VHM.VN", "2026-10-23", 5, benchmark="VNINDEX.VN")
    assert len(list((tmp_path / "settlement-evidence").glob("*.json"))) == 2
    assert path.read_bytes() == raw


def test_failed_price_source_receipt_has_no_provider_error_body(tmp_path, monkeypatch):
    monkeypatch.setattr(settlement, "_now", lambda: pd.Timestamp("2026-11-04T04:00:00Z"))

    def fail(*args, **kwargs):
        raise ValueError("private URL or credentials must not enter price receipt")

    monkeypatch.setattr(vietnam, "load", fail)
    original = settlement.fetch_returns
    cfg = {"data_cache_dir": str(tmp_path), "desktop_vietnam": {"enabled": True}}
    with pytest.raises(ValueError, match="private URL"), vietnam.settlement_scope(cfg) as receipts:
        settlement.fetch_returns("VIC.VN", "2026-10-23", benchmark="VNINDEX.VN")
    proof = json.loads((tmp_path / "settlement-evidence" / receipts[0]["file"]).read_text())
    assert proof["status"] == "failed" and proof["error_type"] == "ValueError"
    assert "private URL" not in json.dumps(proof) and proof["price_reads"] == []
    assert settlement.fetch_returns is original
