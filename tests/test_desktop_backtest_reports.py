"""Grid reports use the existing reader without importing later outcome lessons."""

import copy
import json

import pytest

from desktop import bridge, integration
from tradingagents.default_config import DEFAULT_CONFIG


@pytest.mark.parametrize("has_saved_context", [True, False])
def test_backtest_report_preserves_analysis_memory_and_declares_missing_legacy_context(
    tmp_path, has_saved_context
):
    folder = tmp_path / "backtest" / "synthetic_grid" / "evidence" / "VIC.VN" / "2026-10-23"
    folder.mkdir(parents=True)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["results_dir"] = str(tmp_path)
    state = {
        "company_of_interest": "VIC.VN",
        "trade_date": "2026-10-23",
        "market_report": "Synthetic technical report",
        "final_rating": "Hold",
        "final_trade_decision": "Rating: Hold\nSynthetic decision",
    }
    if has_saved_context:
        state.update(
            past_context="Lesson already available before analysis",
            memory_note="Synthetic memory status",
        )
    # A later lesson exists elsewhere, but is never read into this report.
    (tmp_path / "trading_memory.md").write_text(
        "Future outcome lesson, unavailable at analysis time"
    )
    original_state = copy.deepcopy(state)
    extra = {"paper_proposal": {"status": "DISABLED"}}
    with integration.report_publication(folder, None):
        path = integration.backtest_report(folder, "VIC.VN", state, {}, cfg, extra)
    assert state == original_state and "evaluation_context" not in extra
    memory = (folder / "6_memory/memory.md").read_text()
    assert "Future outcome lesson" not in memory
    if has_saved_context:
        assert "Lesson already available before analysis" in memory
    else:
        assert "không giữ context Memory" in memory
        assert "Chưa có bài học được cung cấp" not in memory
    text = path.read_text()
    assert "Backtest · phân tích hồi cứu" in text and "không phải lợi nhuận danh mục" in text
    assert "Backtest · phân tích hồi cứu" in path.with_suffix(".html").read_text()
    metadata = json.loads((folder / "integration.json").read_text())
    assert metadata["evaluation_context"]["analysis_date"] == "2026-10-23"
    items = bridge.history(cfg)
    assert len(items) == 1 and items[0]["backtest"]
    assert bridge.report_path(items[0]["id"], cfg) == path
