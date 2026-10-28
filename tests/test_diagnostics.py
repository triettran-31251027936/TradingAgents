"""Doctor checks the actual graph without inference or account discovery."""

import copy
import json
import sys

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.diagnostics import diagnose


def test_doctor_constructs_all_roles_without_inference(tmp_path, monkeypatch):
    from tradingagents.llm_clients.cli_client import CLIChatModel

    def no_inference(*args, **kwargs):
        raise AssertionError("offline doctor called a model")

    monkeypatch.setattr(CLIChatModel, "_execute", no_inference)
    monkeypatch.setenv("TRADINGAGENTS_CODEX_BIN", sys.executable)
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(llm_provider="codex_cli", quick_think_llm="default", deep_think_llm="default",
                  data_cache_dir=str(tmp_path / "cache"), results_dir=str(tmp_path / "results"),
                  memory_log_path=str(tmp_path / "memory.md"), checkpoint_enabled=True,
                  backend_url=None, output_language="Vietnamese")
    report = diagnose(config)
    assert report["status"] == "pass"
    assert report["live_inference_tested"] is False
    assert report["market_data_tested"] is False
    assert report["analysts"] == ["market", "social", "news", "fundamentals"]
    assert {"Research Manager", "Trader", "Portfolio Manager", "Memory Log",
            "Bull Researcher", "Bear Researcher", "Aggressive Analyst",
            "Conservative Analyst", "Neutral Analyst"} <= set(report["graph_nodes"])
    assert not (tmp_path / "memory.md").exists(), "doctor must not record a fake decision"


def test_doctor_report_excludes_credentials_and_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_CODEX_BIN", sys.executable)
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(llm_provider="codex_cli", quick_think_llm="default", deep_think_llm="default",
                  data_cache_dir=str(tmp_path / "cache"), results_dir=str(tmp_path / "results"),
                  memory_log_path=str(tmp_path / "memory.md"), backend_url=None,
                  api_key="do-not-report-this", private_endpoint="https://private.example")
    report = json.dumps(diagnose(config))
    assert "do-not-report-this" not in report and "private.example" not in report
