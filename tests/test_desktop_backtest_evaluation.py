"""Later outcomes remain bound to the exact grid decision, outside role context."""



from desktop import backtest_evaluation as evaluation, bridge
from tradingagents.memory import TradingMemoryLog


def setup(root):
    cfg = {"results_dir": str(root)}
    folder = root / "backtest/grid/evidence/VHM.VN/2026-10-23"
    folder.mkdir(parents=True)
    report = folder / "complete_report.md"
    report.write_text("# Synthetic saved VHM report\n")
    decision = "Rating: Hold\nSynthetic original analysis"
    (folder / "5_portfolio").mkdir()
    (folder / "5_portfolio/decision.md").write_text(decision)
    (folder / "6_memory").mkdir()
    (folder / "6_memory/memory.md").write_text("Original context only")
    memory = TradingMemoryLog({"memory_log_path": str(root / "backtest/grid/trading_memory.md")})
    return cfg, report, decision, memory


def test_missing_outcome_log_does_not_invent_a_zero_return(tmp_path):
    cfg, report, _, _ = setup(tmp_path)
    value = evaluation.section(report, cfg)
    assert value["status"] == "unavailable" and "Chưa có log" in value["text"]
    assert "Lợi nhuận chuỗi giá" not in value["text"]


def test_pending_decision_is_not_scored_or_given_a_fake_reflection(tmp_path):
    cfg, report, decision, memory = setup(tmp_path)
    memory.store_decision("VHM.VN", "2026-10-23", decision, rating="Hold")
    value = evaluation.section(report, cfg)
    assert value["status"] == "pending" and "Còn chờ: 1" in value["text"]
    assert "Phản tư sau kết quả" not in value["text"]
    assert "Lợi nhuận chuỗi giá" not in value["text"]


def test_later_outcome_reflection_is_readable_but_never_replaces_original_memory(
    tmp_path, monkeypatch
):
    cfg, report, decision, memory = setup(tmp_path)
    memory.store_decision("VHM.VN", "2026-10-23", decision, rating="Hold")
    memory.update_with_outcome(
        "VHM.VN", "2026-10-23", -0.03, -0.01, 5, "Synthetic later lesson", "2026-11-02"
    )
    before = (report.parent / "6_memory/memory.md").read_bytes()
    monkeypatch.setattr(bridge, "config", lambda: cfg)
    value = bridge.dispatch("report", {"id": str(report.relative_to(tmp_path))})
    outcome = next(s for s in value["sections"] if s["name"] == "Đánh giá sau phân tích")
    assert outcome["status"] == "resolved" and "Synthetic later lesson" in outcome["text"]
    assert "-3.0%" in outcome["text"] and "-1.0%" in outcome["text"]
    assert (
        "Hold không dự báo chiều giá" in outcome["text"] and "Đúng chiều: 0" not in outcome["text"]
    )
    assert "2026-11-02" in outcome["text"] and "không phải lãi/lỗ" in outcome["text"]
    assert "Synthetic later lesson" in outcome["html"]
    assert (report.parent / "6_memory/memory.md").read_bytes() == before
    assert "Synthetic later lesson" not in value["markdown"]
    assert (
        next(s for s in value["sections"] if s["name"] == "memory")["text"]
        == "Original context only"
    )
    assert not memory.get_past_context("VHM.VN", as_of="2026-11-01")


def test_different_decision_same_ticker_date_cannot_inherit_outcome(tmp_path):
    cfg, report, decision, memory = setup(tmp_path)
    memory.store_decision("VHM.VN", "2026-10-23", decision + " different run", rating="Hold")
    value = evaluation.section(report, cfg)
    assert value["status"] == "decision_mismatch" and "không khớp" in value["text"]
    assert "Tổng hợp" not in value["text"]


def test_symlinked_log_outside_run_is_not_opened(tmp_path):
    cfg, report, _, _ = setup(tmp_path)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("Should not be opened")
    (tmp_path / "backtest/grid/trading_memory.md").symlink_to(outside)
    value = evaluation.section(report, cfg)
    assert value["status"] == "unavailable"
    assert "Should not be opened" not in value["text"]


def test_live_report_does_not_get_a_grid_outcome(tmp_path):
    folder = tmp_path / "reports/VIC_test"
    folder.mkdir(parents=True)
    report = folder / "complete_report.md"
    report.write_text("Live report")
    assert evaluation.section(report, {"results_dir": str(tmp_path)}) is None
