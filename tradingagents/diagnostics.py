"""Offline installation/graph checks for desktop-terminal launchers."""

import shutil

import tradingagents
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.factory import tier_provider


def diagnose(config):
    """Construct the real graph without inference, data fetching or decisions.

    The allowlist deliberately excludes environment dumps, endpoints and keys.
    A pass verifies wiring and executable discovery, not account access or data.
    """
    report = {
        "version": tradingagents.__version__,
        "check": "offline installation and graph construction",
        "live_inference_tested": False,
        "market_data_tested": False,
        "tiers": {},
    }
    for tier in ("quick", "deep"):
        provider = tier_provider(config, tier)
        report["tiers"][tier] = {"provider": provider, "model": config[f"{tier}_think_llm"]}
    graph = TradingAgentsGraph(config=config)
    report["analysts"] = list(graph.selected_analysts)
    report["graph_nodes"] = sorted(graph.graph.get_graph().nodes)
    report["checkpoint_enabled"] = config.get("checkpoint_enabled", False)
    report["output_language"] = config.get("output_language")
    # CLIClient has already resolved overrides and verified the executable.
    for tier, model in (("quick", graph.quick_thinking_llm), ("deep", graph.deep_thinking_llm)):
        executable = getattr(model, "executable", None)
        if executable:
            report["tiers"][tier]["executable"] = shutil.which(executable)
    report["status"] = "pass"
    return report
