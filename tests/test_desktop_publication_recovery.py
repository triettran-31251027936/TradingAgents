"""Committed receipt recovery uses native recording, no models or approvals."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from desktop import integration, publication_recovery as recovery
from desktop.practice import PracticeStore
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.memory import TradingMemoryLog
from tradingagents.reporting import write_report_tree

KEY = "a" * 64


def inputs(root):
    cfg = {
        "results_dir": str(root / "reports"),
        "data_cache_dir": str(root / "cache"),
        "memory_log_path": str(root / "memory.md"),
        "checkpoint_enabled": False,
    }
    state = {
        "company_of_interest": "VIC.VN",
        "trade_date": "2026-10-23",
        "market_report": "Synthetic market",
        "sentiment_report": "Synthetic sentiment",
        "news_report": "Synthetic news",
        "fundamentals_report": "Synthetic fundamentals",
        "investment_debate_state": dict.fromkeys(
            ("bull_history", "bear_history", "history", "current_response"), "Synthetic research"
        ),
        "risk_debate_state": dict.fromkeys(
            ("aggressive_history", "conservative_history", "neutral_history", "history"),
            "Synthetic risk",
        ),
        "trader_investment_plan": "Synthetic plan",
        "investment_plan": "Synthetic investment plan",
        "final_trade_decision": "Rating: Hold\nSynthetic committed decision",
        "final_rating": "Hold",
        "past_context": "Only pre-analysis context",
        "memory_note": "Synthetic memory status",
    }
    return cfg, state


def seed(root):
    cfg, state = inputs(root)
    folder = Path(cfg["results_dir"]) / "reports" / "synthetic"
    folder.mkdir(parents=True)
    store = PracticeStore(cfg["data_cache_dir"])
    with integration.report_publication(folder, store) as writer:
        path = write_report_tree(state, "VIC.VN", folder, settings={})
        result = {
            "ticker": "VIC.VN",
            "date": "2026-10-23",
            "rating": "Hold",
            "report_id": str(path.relative_to(Path(cfg["results_dir"]))),
            "paper_proposal": {"status": "NO_ORDER"},
            "paid_budget": {"deepseek_attempts": 1},
        }
        payload = recovery.receipt(
            folder, cfg, "VIC.VN", "2026-10-23", "synthetic", {}, state, result, None
        )
        writer.receipt(KEY, payload)
    return cfg, state, store, folder


def no_models(*args, **kwargs):
    raise AssertionError("Recovery must not construct a model/graph or approve an order")


def test_real_process_exit_after_sqlite_commit_recovers_with_native_recorder(tmp_path, monkeypatch):
    script = """
import os, runpy, sys
from pathlib import Path
helpers=runpy.run_path(sys.argv[1])
unlink=Path.unlink

def crash(path,*args,**kwargs):
    if path.name=='.publishing':os._exit(42)
    return unlink(path,*args,**kwargs)
Path.unlink=crash
helpers['seed'](Path(sys.argv[2]))
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).resolve()), str(tmp_path)],
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == 42, process.stderr.decode()
    cfg, state = inputs(tmp_path)
    store = PracticeStore(cfg["data_cache_dir"])
    assert store.pending_publication(KEY) is not None
    before = store.snapshot()
    monkeypatch.setattr(TradingAgentsGraph, "__init__", no_models)
    monkeypatch.setattr(PracticeStore, "approve", no_models)
    result = recovery.recover(store, KEY, cfg, "VIC.VN", "2026-10-23", TradingAgentsGraph)
    assert result["recovered_publication"] and result["paid_budget"]["deepseek_attempts"] == 1
    assert store.snapshot() == before
    entries = TradingMemoryLog(cfg).load_entries()
    assert len(entries) == 1 and entries[0]["decision"] == state["final_trade_decision"]
    native_log = (
        Path(cfg["results_dir"])
        / "VIC.VN/TradingAgentsStrategy_logs/full_states_log_2026-10-23.json"
    )
    assert (
        json.loads(native_log.read_text())["final_trade_decision"] == state["final_trade_decision"]
    )
    assert store.pending_publication(KEY) is None
    assert recovery.recover(store, KEY, cfg, "VIC.VN", "2026-10-23", TradingAgentsGraph) is None
    assert not list(Path(cfg["results_dir"]).rglob(".publishing"))


@pytest.mark.parametrize("damage", ["report", "decision", "missing", "escape", "state"])
def test_changed_or_corrupt_artifacts_fail_before_any_recording_or_model(
    tmp_path, monkeypatch, damage
):
    cfg, _, store, folder = seed(tmp_path)
    before = store.snapshot()
    payload = store.pending_publication(KEY)
    if damage == "report":
        (folder / "complete_report.md").write_text("Changed report")
    elif damage == "decision":
        (folder / "5_portfolio/decision.md").write_text("Changed decision")
    elif damage == "missing":
        (folder / "complete_report.md").unlink()
    else:
        payload = copy.deepcopy(payload)
        if damage == "escape":
            payload["files"]["../../outside.md"] = "0" * 64
        else:
            payload["state"]["company_of_interest"] = "VHM.VN"
        with store.connect() as db:
            db.execute("UPDATE publications SET payload=? WHERE id=?", (json.dumps(payload), KEY))
    monkeypatch.setattr(TradingAgentsGraph, "__init__", no_models)
    monkeypatch.setattr(TradingAgentsGraph, "record_decision", no_models)
    monkeypatch.setattr(PracticeStore, "approve", no_models)
    with pytest.raises(ValueError, match="không gọi model"):
        recovery.recover(store, KEY, cfg, "VIC.VN", "2026-10-23", TradingAgentsGraph)
    assert store.snapshot() == before and store.pending_publication(KEY) is not None
    assert not Path(cfg["memory_log_path"]).exists()


def test_request_lock_prevents_second_live_process_from_starting_same_request(tmp_path):
    cfg = {"data_cache_dir": str(tmp_path)}
    script = """
import sys
from desktop.publication_recovery import request_lock
try:
    with request_lock({'data_cache_dir':sys.argv[1]},sys.argv[2]):
        raise AssertionError('Second process acquired the live request')
except ValueError as exc:
    assert 'tiến trình khác' in str(exc)
"""
    with recovery.request_lock(cfg, KEY):
        process = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), KEY], capture_output=True, timeout=30
        )
        assert process.returncode == 0, process.stderr.decode()
    with recovery.request_lock(cfg, KEY):
        pass


def test_startup_skips_live_owner_then_recovers_without_model_initialization(tmp_path, monkeypatch):
    from desktop import bridge
    from tradingagents.default_config import DEFAULT_CONFIG

    cfg, _, store, _ = seed(tmp_path)
    cfg = {
        **copy.deepcopy(DEFAULT_CONFIG),
        **cfg,
        "desktop_vietnam": {"enabled": True, "paper_enabled": True},
    }
    monkeypatch.setattr(TradingAgentsGraph, "__init__", no_models)
    monkeypatch.setattr(PracticeStore, "approve", no_models)
    monkeypatch.setattr(bridge, "config", lambda: cfg)
    with recovery.request_lock(cfg, KEY):
        result = recovery.resume_pending(cfg, TradingAgentsGraph)
        assert result == {"finished": [], "busy": [KEY], "failed": []}
        assert not Path(cfg["memory_log_path"]).exists()
    meta = bridge.dispatch("meta", {})
    assert meta["publication_recovery"] == {"finished": [KEY], "busy": [], "failed": []}
    assert len(meta["history"]) == 1 and store.pending_publication(KEY) is None
    assert len(TradingMemoryLog(cfg).load_entries()) == 1
    assert store.snapshot()["cash"] == 3_000_000 and not store.snapshot()["orders"]


def test_corrupt_receipt_is_reported_without_blocking_startup_or_falling_back_to_models(
    tmp_path, monkeypatch
):
    cfg, _, store, _ = seed(tmp_path)
    cfg["desktop_vietnam"] = {"paper_enabled": True}
    with store.connect() as db:
        db.execute("UPDATE publications SET payload='not json' WHERE id=?", (KEY,))
    monkeypatch.setattr(TradingAgentsGraph, "__init__", no_models)
    result = recovery.resume_pending(cfg, TradingAgentsGraph)
    assert result == {"finished": [], "busy": [], "failed": [KEY]}
    assert not Path(cfg["memory_log_path"]).exists()


def test_output_display_options_do_not_fork_the_completed_request_identity():
    args = ({"model": "synthetic"}, "VHM.VN", "2026-10-23", ["market"], "stock", None)
    assert recovery.identity(
        *args, {"save": True, "html": True, "show": False}
    ) == recovery.identity(*args, {"save": False, "html": False, "show": True})


def test_bad_receipt_identity_cannot_write_outside_lock_directory(tmp_path, monkeypatch):
    cfg, _, store, _ = seed(tmp_path)
    cfg["desktop_vietnam"] = {"paper_enabled": True}
    with store.connect() as db:
        db.execute("UPDATE publications SET id='../outside' WHERE id=?", (KEY,))
    monkeypatch.setattr(TradingAgentsGraph, "__init__", no_models)
    assert recovery.resume_pending(cfg, TradingAgentsGraph)["failed"] == ["../outside"]
    assert not (tmp_path / "cache/outside.lock").exists()
    assert not Path(cfg["memory_log_path"]).exists()


def test_corrupt_account_storage_does_not_crash_metadata_recovery(tmp_path, monkeypatch):
    folder = tmp_path / "paper"
    folder.mkdir()
    db = folder / "practice.sqlite"
    raw = b"not a sqlite database"
    db.write_bytes(raw)
    cfg = {"data_cache_dir": str(tmp_path), "desktop_vietnam": {"paper_enabled": True}}
    monkeypatch.setattr(TradingAgentsGraph, "__init__", no_models)
    assert recovery.resume_pending(cfg, TradingAgentsGraph) == {
        "finished": [],
        "busy": [],
        "failed": ["account_storage"],
    }
    assert db.read_bytes() == raw
