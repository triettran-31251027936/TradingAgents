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
                            response_metadata={"finish_reason": "length"},
                        ),
                    ),
                    "Synthetic evidence",
                    {},
                )
                return {"market_report": "Synthetic partial"}

            def manager(state):
                control["manager"] += 1
                runtime = control["runtimes"][-1]
                value = ExecutablePortfolioDecision(
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
