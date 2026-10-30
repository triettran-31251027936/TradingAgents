"""Resume must preserve per-role Memory transport, budget, receipts and PIT."""

import copy
import json

from desktop.integration import backtest_scope
from desktop.model_routing import ROLE_NAMES
from desktop.runtime_models import Runtime
from tests.test_graph_end_to_end import ScriptedModel, _Client
from tradingagents import backtest
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph import trading_graph
from tradingagents.memory import TradingMemoryLog, settlement


def test_memory_prompt_preserves_hold_and_return_semantics(tmp_path):
    runtime = Runtime(
        {"data_cache_dir": str(tmp_path), "output_language": "Vietnamese"},
        quant={"ticker": "FPT.VN"},
    )
    prompt = runtime.prompt("Review a synthetic Hold decision", "Memory Log")
    instructions = prompt[0].content
    assert "Hold claims NO direction" in instructions
    assert "NOT account P&L" in instructions
    assert "synthetic decision's test status" in instructions
    assert "reflection in Vietnamese" in instructions
    assert "FPT.VN" not in instructions


def test_all_skipped_resume_still_routes_memory_and_caps_reflection(tmp_path, monkeypatch):
    import tradingagents.llm_clients as clients

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(
        results_dir=str(tmp_path / "results"),
        data_cache_dir=str(tmp_path / "cache"),
        llm_provider="codex_cli",
        quick_think_provider="codex_cli",
        deep_think_provider="codex_cli",
        desktop_usage={"enabled": True, "cache_enabled": False},
        desktop_budget={
            "enabled": True,
            "deepseek_max_requests": 1,
            "deepseek_max_output_tokens": 512,
            "deepseek_in_backtest": True,
        },
        desktop_role_models={
            role: {
                "provider": "deepseek" if role == "Memory Log" else "codex_cli",
                "model": "deepseek-flash" if role == "Memory Log" else "gpt-6.1-sol",
            }
            for role in ROLE_NAMES
        },
    )
    cfg["benchmark_map"] = {**cfg["benchmark_map"], ".VN": "VNINDEX.VN"}
    run_id = "resume_memory_only"
    log_path = tmp_path / "results/backtest" / run_id / "trading_memory.md"
    memory = TradingMemoryLog({"memory_log_path": str(log_path)})
    for ticker in ["VCB.VN", "FPT.VN"]:
        memory.store_decision(
            ticker, "2026-01-09", "Rating: Hold\nA synthetic test decision; no actual trade."
        )
    models = {p: ScriptedModel() for p in ["deepseek", "codex_cli"]}

    def client(config, tier, **kw):
        provider = config.get(tier + "_think_provider") or config["llm_provider"]
        return _Client(models[provider].model_copy(update={"callbacks": kw.get("callbacks", [])}))

    monkeypatch.setattr(clients, "create_tier_client", client)
    monkeypatch.setattr(trading_graph, "create_tier_client", client)
    benchmarks = []

    def returns(ticker, day, holding_days, benchmark):
        benchmarks.append(benchmark)
        return 0.03, 0.01, holding_days, "2026-01-20"

    monkeypatch.setattr(settlement, "fetch_returns", returns)
    original = backtest.TradingAgentsGraph
    with backtest_scope(cfg, lambda *a: None):
        first = backtest.run_backtest(["VCB.VN", "FPT.VN"], ["2026-01-09"], cfg, run_id=run_id)
    assert backtest.TradingAgentsGraph is original
    assert first.cells_run == 0 and first.skipped == 2
    assert len(first.settlement_failures) == 1
    assert len(models["deepseek"].calls) == 1 and not models["codex_cli"].calls
    assert backtest.summarize(first).resolved == 1
    assert memory.get_past_context("VCB.VN", as_of="2026-01-19") == ""
    assert memory.get_past_context("VCB.VN", as_of="2026-01-21")
    folder = log_path.parent / "evidence"
    receipt = json.loads((folder / "memory-settlement.json").read_text())
    assert receipt["paid_budget"]["deepseek_attempts"] == 1
    assert receipt["roles"]["Memory Log"]["calls"] == 1
    assert receipt["usage"]["items"][0]["provider"] == "deepseek"
    assert receipt["usage"]["items"][0]["role"] == "Memory Log"
    # A second explicit resume gets its own bounded pass, retains the first
    # pass's receipt, and settles only the still-pending entry.
    with backtest_scope(cfg, lambda *a: None):
        second = backtest.run_backtest(["VCB.VN", "FPT.VN"], ["2026-01-09"], cfg, run_id=run_id)
    assert second.cells_run == 0 and second.skipped == 2
    assert not second.settlement_failures
    assert backtest.summarize(second).resolved == 2
    assert len(models["deepseek"].calls) == 2 and not models["codex_cli"].calls
    assert set(benchmarks) == {"VNINDEX.VN"}
    assert len(list((folder / "settlement").glob("*.json"))) == 2
