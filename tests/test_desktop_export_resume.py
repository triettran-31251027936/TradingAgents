"""Keep native finished checkpoints until desktop evidence exports succeed."""

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from desktop import bridge, integration
from desktop.runtime_models import ExecutablePortfolioDecision
from tradingagents import backtest
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph import trading_graph
from tradingagents.graph.checkpointer import checkpoint_step


class State(TypedDict, total=False):
    company_of_interest: str
    trade_date: str
    market_report: str
    final_trade_decision: str
    final_rating: str


def fixture_graph(monkeypatch, control):
    class Graph(trading_graph.TradingAgentsGraph):
        instances = []

        def __init__(self, selected_analysts=None, *, config, callbacks=(), **kwargs):
            self.config = copy.deepcopy(config)
            self.selected_analysts = selected_analysts or ["market"]
            self.callbacks = list(callbacks)
            self._checkpointer_ctx = None
            self.debug = False
            self.propagator = SimpleNamespace(
                get_graph_args=lambda **kw: {
                    "config": {"callbacks": kw.get("callbacks", [])},
                }
            )
            self.resolve_instrument_context = lambda *a, **kw: "Synthetic instrument"

            def market(state):
                control["market"] += 1
                runtime = control["runtimes"][-1]
                runtime.invoke(
                    "Market Analyst",
                    {"provider": "deepseek"},
                    SimpleNamespace(
                        invoke=lambda *a, **kw: AIMessage(
                            content="Synthetic partial",
                            response_metadata={
                                "finish_reason": control.get("finish_reason", "length")
                            },
                        ),
                    ),
                    "Synthetic evidence",
                    {},
                )
                return {"market_report": "Synthetic partial"}

            def manager(state):
                control["manager"] += 1
                runtime = control["runtimes"][-1]
                value = (
                    ExecutablePortfolioDecision(**control["decision"])
                    if "decision" in control
                    else ExecutablePortfolioDecision(
                        rating="Hold",
                        executive_summary="Synthetic",
                        investment_thesis="Synthetic",
                        execution={
                            "action": "Hold",
                            "quantity": 0,
                            "limit_price": None,
                            "rationale": "Synthetic",
                            "data_gaps": [],
                        },
                    )
                )
                runtime._capture("Portfolio Manager", value)
                runtime.persist()
                return {
                    "final_trade_decision": "Rating: Hold\nSynthetic decision",
                    "final_rating": "Hold",
                }

            builder = StateGraph(State)
            builder.add_node("market", market)
            builder.add_node("manager", manager)
            builder.add_edge(START, "market")
            builder.add_edge("market", "manager")
            builder.add_edge("manager", END)
            self.workflow = builder
            self.graph = builder.compile()
            self.instances.append(self)

        def create_run_state(self, ticker, asof, asset_type="stock", portfolio=None):
            return {"company_of_interest": ticker, "trade_date": asof}

        def record_decision(self, *args):
            control["record"] += 1

    def routing(graph, routes, callbacks=(), runtime=None):
        if runtime:
            control["runtimes"].append(runtime)

    monkeypatch.setattr(integration, "apply_role_models", routing)
    return Graph


@pytest.mark.parametrize("mode", ["analysis", "backtest"])
def test_completed_graph_survives_export_failure_and_retries_without_models(
    tmp_path, monkeypatch, mode
):
    control = {"market": 0, "manager": 0, "record": 0, "runtimes": []}
    Graph = fixture_graph(monkeypatch, control)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(
        checkpoint_enabled=True,
        data_cache_dir=str(tmp_path / "cache"),
        results_dir=str(tmp_path / "reports"),
        memory_log_path=str(tmp_path / "memory.md"),
        desktop_usage={"enabled": True, "cache_enabled": False},
        desktop_budget={"enabled": True, "deepseek_max_requests": 1},
        desktop_vietnam={"enabled": False},
    )
    monkeypatch.setattr(bridge, "emit", lambda *args: None)
    original = integration.artifacts
    fail = {"enabled": True}

    def export(*args, **kwargs):
        if fail["enabled"]:
            raise OSError("Synthetic export failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(integration, "artifacts", export)
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    monkeypatch.setattr(backtest, "TradingAgentsGraph", Graph)

    def run():
        if mode == "analysis":
            return bridge.analyze(
                {"ticker": "FPT.VN", "date": "2026-01-09", "analysts": ["market"]}, cfg
            )
        with integration.backtest_scope(cfg, lambda *args: None):
            graph = backtest.TradingAgentsGraph(["market"], config=cfg)
            return graph.propagate("FPT.VN", "2026-01-09")

    with pytest.raises(OSError, match="Synthetic export"):
        run()
    first = Graph.instances[-1]
    assert (
        checkpoint_step(
            cfg["data_cache_dir"], "FPT.VN", "2026-01-09", first._run_signature("stock")
        )
        is not None
    )
    assert list((Path(cfg["data_cache_dir"]) / "desktop-checkpoints").glob("*.json"))
    fail["enabled"] = False
    result = run()
    assert control["market"] == 1 and control["manager"] == 1
    runtime = control["runtimes"][-1]
    assert runtime.decision["execution"]["action"] == "Hold"
    assert runtime.paid_budget.calls == 1
    assert runtime.incomplete_roles == {"Market Analyst": "output_token_limit"}
    assert runtime._checkpoint is None
    assert not list((Path(cfg["data_cache_dir"]) / "desktop-checkpoints").glob("*.json"))
    final = Graph.instances[-1]
    assert (
        checkpoint_step(
            cfg["data_cache_dir"], "FPT.VN", "2026-01-09", final._run_signature("stock")
        )
        is None
    )
    if mode == "analysis":
        assert result["rating"] == "Hold"
        assert result["report_quality"]["required_outputs_complete"] is False
    else:
        assert result[0]["market_report"] == "Synthetic partial"


@pytest.mark.parametrize("failure_stage", ["evidence", "report"])
def test_real_grid_does_not_skip_a_cell_whose_evidence_export_failed(
    tmp_path, monkeypatch, failure_stage
):
    from tradingagents.memory import TradingMemoryLog
    from tradingagents.memory.settlement import Settlement

    control = {"market": 0, "manager": 0, "record": 0, "runtimes": []}
    Base = fixture_graph(monkeypatch, control)

    class GridGraph(Base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.memory_log = TradingMemoryLog(self.config)

        def record_decision(self, ticker, asof, state):
            control["record"] += 1
            self.memory_log.store_decision(
                ticker, asof, state["final_trade_decision"], rating="Hold"
            )

        def settle_pending(self, ticker):
            # Outcome collection/reflection is outside this export-resume test.
            return Settlement()

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(
        checkpoint_enabled=True,
        data_cache_dir=str(tmp_path / "cache"),
        results_dir=str(tmp_path / "reports"),
        desktop_usage={"enabled": True, "cache_enabled": False},
        desktop_budget={"enabled": True, "deepseek_max_requests": 1},
        desktop_vietnam={"enabled": False},
    )
    monkeypatch.setattr(backtest, "TradingAgentsGraph", GridGraph)
    method = "artifacts" if failure_stage == "evidence" else "backtest_report"
    original = getattr(integration, method)
    fail = {"enabled": True}

    def export(*args, **kwargs):
        value = original(*args, **kwargs)
        if fail["enabled"]:
            raise OSError("Synthetic evidence export failure")
        return value

    monkeypatch.setattr(integration, method, export)
    with integration.backtest_scope(cfg, lambda *args: None):
        first = backtest.run_backtest(["BSR.VN"], ["2026-01-09"], cfg, run_id="export_resume")
    assert first.cells_run == 0 and len(first.failures) == 1
    assert not TradingMemoryLog({"memory_log_path": str(first.log_path)}).load_entries()
    assert control["record"] == 0
    assert bridge.history(cfg) == []
    assert list(Path(cfg["results_dir"]).rglob(".publishing"))
    fail["enabled"] = False
    with integration.backtest_scope(cfg, lambda *args: None):
        resumed = backtest.run_backtest(["BSR.VN"], ["2026-01-09"], cfg, run_id="export_resume")
    assert resumed.cells_run == 1 and resumed.skipped == 0 and not resumed.failures
    assert control["market"] == control["manager"] == control["record"] == 1
    reports = bridge.history(cfg)
    assert len(reports) == 1 and reports[0]["backtest"]
    report = bridge.report_path(reports[0]["id"], cfg)
    assert report.with_suffix(".html").is_file()
    assert (report.parent / "1_analysts/market.md").read_text() == "Synthetic partial"
    assert (report.parent / "5_portfolio/decision.md").is_file()
    assert (report.parent / "integration.json").is_file()
    assert not (report.parent / ".publishing").exists()
    assert len(TradingMemoryLog({"memory_log_path": str(resumed.log_path)}).load_entries()) == 1
    assert not list((tmp_path / "cache/desktop-checkpoints").glob("*.json"))


def test_paper_export_failure_resumes_same_graph_without_duplicate_proposal(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from desktop.practice import PracticeStore
    from desktop.vietnam import digest

    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
    control = {"market": 0, "manager": 0, "record": 0, "runtimes": [], "finish_reason": "stop"}
    control["decision"] = {
        "rating": "Overweight",
        "executive_summary": "Synthetic",
        "investment_thesis": "Synthetic",
        "execution": {
            "action": "Buy",
            "quantity": 100,
            "limit_price": 1000,
            "rationale": "Synthetic test only",
            "data_gaps": [],
        },
    }
    Graph = fixture_graph(monkeypatch, control)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(
        checkpoint_enabled=True,
        data_cache_dir=str(tmp_path / "cache"),
        results_dir=str(tmp_path / "reports"),
        memory_log_path=str(tmp_path / "memory.md"),
        desktop_usage={"enabled": True, "cache_enabled": False},
        desktop_budget={"enabled": True, "deepseek_max_requests": 1},
        desktop_vietnam={"enabled": False},
    )
    store = PracticeStore(cfg["data_cache_dir"])
    quant = {
        "ticker": "VHM.VN",
        "analysis_date": today,
        "exchange": "HOSE",
        "fingerprint": "synthetic-not-market-data",
        "prices": [{"date": today, "close": 1000}],
        "metrics": {
            key: {
                "value": 1,
                "unit": "synthetic",
                "asof": today,
                "formula": "synthetic",
                "source": "isolated fixture",
            }
            for key in ("close", "adv20", "volatility20_annual", "historical_var95_1d")
        },
        "missing": [],
        "warnings": [],
        "basis": [],
        "sources": [],
    }
    original_prepare = integration.prepare

    def prepare(graph, *args, **kwargs):
        _, runtime, portfolio, _ = original_prepare(graph, *args, **kwargs)
        graph.config["desktop_paper_context_fingerprint"] = digest(store.snapshot())
        graph.config["desktop_quant_fingerprint"] = quant["fingerprint"]
        return quant, runtime, portfolio, store

    monkeypatch.setattr(integration, "prepare", prepare)
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    monkeypatch.setattr(bridge, "emit", lambda *args: None)
    fail = {"enabled": True}
    append = integration.append_evidence

    def append_then_fail(*args, **kwargs):
        append(*args, **kwargs)
        if fail["enabled"]:
            raise OSError("Synthetic failure after proposal and complete report")

    monkeypatch.setattr(integration, "append_evidence", append_then_fail)
    payload = {"ticker": "VHM.VN", "date": today, "analysts": ["market"]}
    original_book = store.snapshot()
    with pytest.raises(OSError, match="Synthetic failure"):
        bridge.analyze(payload, cfg)
    assert store.snapshot() == original_book
    assert control["record"] == 0
    assert bridge.history(cfg) == []
    assert list(Path(cfg["results_dir"]).rglob(".publishing"))
    assert list((Path(cfg["data_cache_dir"]) / "desktop-checkpoints").glob("*.json"))
    fail["enabled"] = False
    result = bridge.analyze(payload, cfg)
    assert control["market"] == control["manager"] == control["record"] == 1
    assert control["runtimes"][-1].paid_budget.calls == 1
    assert result["paper_proposal"]["status"] == "PROPOSED"
    assert result["paper_proposal"]["human_approval_required"]
    book = store.snapshot()
    assert len(book["orders"]) == 1 and book["orders"][0]["status"] == "PROPOSED"
    assert book["cash"] == 3_000_000 and book["positions"] == {}
    assert len(bridge.history(cfg)) == 1
    assert not list((Path(cfg["data_cache_dir"]) / "desktop-checkpoints").glob("*.json"))
