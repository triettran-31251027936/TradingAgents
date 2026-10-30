"""Real SQLite checkpoint recovery through desktop preparation; no LLM inference.

Nodes deliberately stand in for analysis/PM to isolate state recovery and snapshot
identity. This does not certify real provider output or the full production graph.
"""

from copy import deepcopy
from typing import TypedDict

import pytest
from langgraph.graph import END, StateGraph

from desktop import integration
from tradingagents.graph.checkpointer import checkpoint_step
from tradingagents.graph.trading_graph import TradingAgentsGraph


class State(TypedDict):
    market_report: str
    final_trade_decision: str


def graph(tmp_path, monkeypatch, control, fingerprint, model="model-a", paper_book=None):
    g = object.__new__(TradingAgentsGraph)
    g.config = {
        "checkpoint_enabled": True,
        "data_cache_dir": str(tmp_path),
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "desktop_vietnam": {"enabled": True},
        "quick_think_llm": model,
    }
    g.selected_analysts = ("market",)
    g._checkpointer_ctx = None

    def analyst(state):
        control["analyst"] += 1
        return {"market_report": g.config["desktop_quant_fingerprint"]}

    def manager(state):
        control["manager"] += 1
        if control["fail"]:
            raise RuntimeError("Controlled PM interruption")
        return {"final_trade_decision": "Hold using " + state["market_report"]}

    flow = StateGraph(State)
    flow.add_node("analyst", analyst)
    flow.add_node("manager", manager)
    flow.set_entry_point("analyst")
    flow.add_edge("analyst", "manager")
    flow.add_edge("manager", END)
    g.workflow = flow
    g.graph = flow.compile()
    q = {
        "ticker": "VCB.VN",
        "company_name": "VCB test fixture",
        "exchange": "HOSE",
        "analysis_date": "2026-10-30",
        "prices": [{"date": "2026-10-30", "close": 50000}],
        "metrics": {"close": {"value": 50000, "status": "available"}},
        "missing": [],
        "basis": [],
        "warnings": [],
        "fingerprint": fingerprint,
    }
    monkeypatch.setattr(integration.vietnam, "load", lambda *a, **kw: deepcopy(q))
    monkeypatch.setattr(integration, "apply_role_models", lambda *a, **kw: None)
    if paper_book is not None:
        import desktop.practice

        class Store:
            def __init__(self, *args):
                pass

            def snapshot(self):
                return deepcopy(paper_book)

        monkeypatch.setattr(desktop.practice, "PracticeStore", Store)
        g.config["desktop_vietnam"]["paper_enabled"] = True
    _, _, g.prepared_portfolio, _ = integration.prepare(
        g,
        "VCB.VN",
        "2026-10-30",
        None,
        g.config,
        [],
        lambda *a: None,
        allow_paper=paper_book is not None,
    )
    return g


def run(g):
    state = {}
    with g.checkpoint_scope("VCB.VN", "2026-10-30", "stock", g.prepared_portfolio) as tid:
        initial = {"market_report": "", "final_trade_decision": ""}
        for _, chunk in g.stream_run(
            g.checkpoint_input(initial), config={"configurable": {"thread_id": tid}}
        ):
            if chunk:
                state.update(chunk)
    return tid, state


def test_same_snapshot_resumes_pm_without_repeating_analysis(tmp_path, monkeypatch):
    control = {"analyst": 0, "manager": 0, "fail": True}
    first = graph(tmp_path, monkeypatch, control, "snapshot-a")
    signature = first._run_signature("stock")
    with pytest.raises(RuntimeError, match="Controlled PM"):
        run(first)
    assert checkpoint_step(tmp_path, "VCB.VN", "2026-10-30", signature) is not None
    control["fail"] = False
    resumed = graph(tmp_path, monkeypatch, control, "snapshot-a")
    _, state = run(resumed)
    assert control == {"analyst": 1, "manager": 2, "fail": False}
    assert state["market_report"] == "snapshot-a"
    assert state["final_trade_decision"] == "Hold using snapshot-a"
    resumed.clear_checkpoint_on_success("VCB.VN", "2026-10-30", "stock")
    assert checkpoint_step(tmp_path, "VCB.VN", "2026-10-30", signature) is None


@pytest.mark.parametrize("change", ["snapshot", "model"])
def test_changed_evidence_or_model_cannot_resume_old_reports(tmp_path, monkeypatch, change):
    control = {"analyst": 0, "manager": 0, "fail": True}
    first = graph(tmp_path, monkeypatch, control, "snapshot-a")
    old_signature = first._run_signature("stock")
    with pytest.raises(RuntimeError, match="Controlled PM"):
        run(first)
    control["fail"] = False
    fresh = graph(
        tmp_path,
        monkeypatch,
        control,
        "snapshot-b" if change == "snapshot" else "snapshot-a",
        "model-b" if change == "model" else "model-a",
    )
    assert fresh._run_signature("stock") != old_signature
    _, state = run(fresh)
    assert control["analyst"] == 2 and control["manager"] == 2
    assert state["final_trade_decision"] == "Hold using " + (
        "snapshot-b" if change == "snapshot" else "snapshot-a"
    )
    assert checkpoint_step(tmp_path, "VCB.VN", "2026-10-30", old_signature) is not None


def paper_book():
    return {
        "cash": 3000000,
        "available_cash": 3000000,
        "nav": 3000000,
        "positions": {},
        "orders": [],
        "max_position_fraction": 1,
        "commission": 0.0015,
        "mode": "test-practice",
        "clock": "2026-10-30",
        "fingerprint": "same-approval-fingerprint",
    }


def test_new_pending_order_invalidates_resume_even_with_same_portfolio(tmp_path, monkeypatch):
    control = {"analyst": 0, "manager": 0, "fail": True}
    book = paper_book()
    first = graph(tmp_path, monkeypatch, control, "snapshot-a", paper_book=book)
    signature = first._run_signature("stock", first.prepared_portfolio)
    with pytest.raises(RuntimeError, match="Controlled PM"):
        run(first)
    changed = deepcopy(book)
    changed["orders"] = [
        {"id": "pending-fixture", "status": "PENDING_APPROVAL", "approved_at": None}
    ]
    control["fail"] = False
    fresh = graph(tmp_path, monkeypatch, control, "snapshot-a", paper_book=changed)
    assert fresh.prepared_portfolio.fingerprint() == first.prepared_portfolio.fingerprint()
    assert fresh._run_signature("stock", fresh.prepared_portfolio) != signature
    run(fresh)
    assert control["analyst"] == 2
    assert book["fingerprint"] == changed["fingerprint"]
    assert checkpoint_step(tmp_path, "VCB.VN", "2026-10-30", signature) is not None


def test_identical_paper_book_resumes_without_repeating_analysis(tmp_path, monkeypatch):
    control = {"analyst": 0, "manager": 0, "fail": True}
    book = paper_book()
    first = graph(tmp_path, monkeypatch, control, "snapshot-a", paper_book=book)
    with pytest.raises(RuntimeError, match="Controlled PM"):
        run(first)
    control["fail"] = False
    resumed = graph(tmp_path, monkeypatch, control, "snapshot-a", paper_book=deepcopy(book))
    assert (
        resumed.config["desktop_paper_context_fingerprint"]
        == first.config["desktop_paper_context_fingerprint"]
    )
    _, state = run(resumed)
    assert control["analyst"] == 1 and state["final_trade_decision"] == "Hold using snapshot-a"


def test_explicit_portfolio_does_not_keep_old_paper_context_identity(tmp_path, monkeypatch):
    control = {"analyst": 0, "manager": 0, "fail": False}
    g = graph(tmp_path, monkeypatch, control, "snapshot-a", paper_book=paper_book())
    assert "desktop_paper_context_fingerprint" in g.config
    integration.prepare(
        g,
        "VCB.VN",
        "2026-10-30",
        g.prepared_portfolio,
        g.config,
        [],
        lambda *a: None,
        allow_paper=True,
    )
    assert "desktop_paper_context_fingerprint" not in g.config
