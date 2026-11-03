"""Report export and paper proposals commit together; isolated synthetic data only."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from desktop import bridge, integration
from desktop.paper import Store
from desktop.practice import PracticeStore
from desktop.vietnam import digest


def evidence():
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
    return {
        "ticker": "VHM.VN",
        "analysis_date": today,
        "exchange": "HOSE",
        "fingerprint": "synthetic-not-market-data",
        "prices": [{"date": today, "close": 1000}],
        "metrics": {
            key: {"value": 1}
            for key in ("close", "adv20", "volatility20_annual", "historical_var95_1d")
        },
        "missing": [],
        "warnings": [],
        "basis": [],
        "sources": [],
    }


def decision():
    return {
        "execution": {
            "action": "Buy",
            "quantity": 100,
            "limit_price": 1000,
            "rationale": "Synthetic publication test, not a trade recommendation",
            "data_gaps": [],
        }
    }


@pytest.mark.parametrize("store_type", [Store, PracticeStore])
@pytest.mark.parametrize("failure", ["after_proposal", "after_report"])
def test_failed_export_rolls_back_proposal_and_hides_partial_report(tmp_path, store_type, failure):
    store = store_type(tmp_path / "cache")
    original = store.snapshot()
    folder = tmp_path / "reports" / "VHM.VN_test"
    folder.mkdir(parents=True)
    report = folder / "complete_report.md"
    cfg = {"results_dir": str(folder.parent)}
    with (
        pytest.raises(OSError, match="Synthetic export"),
        integration.report_publication(folder, store) as writer,
    ):
        proposal = writer.proposal("test", decision(), evidence(), "HOSE")
        assert proposal["status"] == "PROPOSED"
        # A second connection (including the approval UI) cannot observe it.
        assert store.snapshot() == original
        assert not hasattr(writer, "approve")
        if failure == "after_report":
            report.write_text("# Synthetic report\n")
            assert bridge.history(cfg) == []
            with pytest.raises(ValueError, match="Báo cáo"):
                bridge.report_path("VHM.VN_test/complete_report.md", cfg)
        raise OSError("Synthetic export failure")
    assert store.snapshot() == original
    assert digest(store.snapshot()) == digest(original)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 0
    # Retrying uses a new report directory, just like bridge.analyze.
    next_folder = folder.with_name("VHM.VN_test_1")
    next_folder.mkdir()
    with integration.report_publication(next_folder, store) as writer:
        published = writer.proposal("test_1", decision(), evidence(), "HOSE")
        (next_folder / "complete_report.md").write_text("# Synthetic ready report\n")
    book = store.snapshot()
    assert len(book["orders"]) == 1
    assert book["orders"][0]["status"] == "PROPOSED"
    assert book["cash"] == original["cash"] and book["positions"] == {}
    assert len(bridge.history(cfg)) == 1
    assert bridge.report_path("VHM.VN_test_1/complete_report.md", cfg).is_file()
    assert store.proposal("test_1", decision(), evidence(), "HOSE")["id"] == published["id"]


@pytest.mark.parametrize("store_type", [Store, PracticeStore])
def test_blocked_proposal_does_not_commit_account_changes(tmp_path, store_type):
    store = store_type(tmp_path / "cache")
    original = store.snapshot()
    folder = tmp_path / "report"
    folder.mkdir()
    invalid = decision()
    invalid["execution"]["quantity"] = (
        1  # Whole-lot validation fails after legacy clock initialization.
    )
    with integration.report_publication(folder, store) as writer:
        with pytest.raises(ValueError):
            writer.proposal("blocked", invalid, evidence(), "HOSE")
        (folder / "complete_report.md").write_text("# Synthetic blocked report\n")
    assert store.snapshot() == original
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 0


def test_process_exit_before_export_commit_leaves_no_proposal(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    cache = tmp_path / "cache"
    store = PracticeStore(cache)
    original = store.snapshot()
    folder = tmp_path / "reports" / "interrupted"
    folder.mkdir(parents=True)
    script = """
import os, runpy, sys
from pathlib import Path
from desktop.integration import report_publication
from desktop.practice import PracticeStore
helpers = runpy.run_path(sys.argv[1])
store = PracticeStore(sys.argv[2])
folder = Path(sys.argv[3])
with report_publication(folder, store) as writer:
    writer.proposal('crash', helpers['decision'](), helpers['evidence'](), 'HOSE')
    (folder / 'complete_report.md').write_text('# Synthetic interrupted export\\n')
    os._exit(42)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).resolve()), str(cache), str(folder)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 42, result.stderr.decode()
    assert store.snapshot() == original
    assert bridge.history({"results_dir": str(folder.parent)}) == []
    assert (folder / ".publishing").is_file()
