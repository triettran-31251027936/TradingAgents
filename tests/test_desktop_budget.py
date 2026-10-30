"""Protect a small paid-API budget without changing the original agent graph."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from desktop.budget import DEFAULT, PaidBudget, backtest_config, constrain, validate_config
from desktop.runtime_models import Runtime


def config(tmp_path=None):
    value = {
        "llm_provider": "codex_cli",
        "quick_think_llm": "gpt-6-luna",
        "deep_think_llm": "gpt-6.1-sol",
        "desktop_budget": dict(DEFAULT),
        "desktop_role_models": {
            "Bear Researcher": {
                "provider": "deepseek",
                "model": "deepseek-flash",
                "max_tokens": 1024,
            }
        },
    }
    if tmp_path:
        value["data_cache_dir"] = str(tmp_path)
    return value


def test_only_one_paid_role_is_allowed_by_default():
    cfg = config()
    validate_config(cfg)
    cfg["desktop_role_models"]["Trader"] = {"provider": "deepseek", "model": "deepseek-flash"}
    with pytest.raises(ValueError, match="ngân sách"):
        validate_config(cfg)


def test_more_debate_rounds_require_an_explicit_budget_change():
    cfg = config()
    cfg["max_debate_rounds"] = 2
    with pytest.raises(ValueError, match="ngân sách"):
        validate_config(cfg)


def test_provider_case_cannot_bypass_limit():
    limit = PaidBudget(config())
    limit.reserve("DeepSeek")
    with pytest.raises(ValueError):
        limit.reserve("deepseek")


def test_api_output_is_capped_and_sdk_retries_disabled():
    cfg = config()
    spec = {"provider": "deepseek", "model": "deepseek-flash", "max_tokens": 8192}
    constrained = constrain(cfg, spec)
    assert constrained["max_tokens"] == 1024
    assert constrained["llm_max_retries"] == 0
    assert "max_tokens" not in cfg


def test_other_provider_does_not_use_paid_budget():
    limit = PaidBudget(config())
    for provider in ["codex_cli", "agy_cli"] * 20:
        limit.reserve(provider)
    assert limit.calls == 0


def test_parallel_calls_cannot_overspend_request_cap():
    limit = PaidBudget(config())

    def attempt(_):
        try:
            limit.reserve("deepseek")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        result = list(executor.map(attempt, range(20)))
    assert sum(result) == 1 and limit.calls == 1


def test_exact_cache_hit_does_not_spend_another_request(tmp_path):
    class Backend:
        calls = 0

        def invoke(self, *args, **kw):
            self.calls += 1
            return AIMessage(content="One cached response")

    cfg = config(tmp_path)
    runtime = Runtime(cfg)
    backend = Backend()
    spec = cfg["desktop_role_models"]["Bear Researcher"]
    first = [HumanMessage(content="Same full context")]
    runtime.invoke("Bear Researcher", spec, backend, first, {})
    runtime.invoke("Bear Researcher", spec, backend, first, {})
    with pytest.raises(ValueError, match="giới hạn"):
        runtime.invoke(
            "Bear Researcher", spec, backend, [HumanMessage(content="Changed context")], {}
        )
    assert backend.calls == 1 and runtime.paid_budget.calls == 1


def test_failed_attempt_counts_and_is_not_retried(tmp_path):
    class Backend:
        calls = 0

        def invoke(self, *args, **kw):
            self.calls += 1
            raise ConnectionError("Isolated failure, no API used")

    cfg = config(tmp_path)
    runtime = Runtime(cfg)
    backend = Backend()
    spec = cfg["desktop_role_models"]["Bear Researcher"]
    value = [HumanMessage(content="Context")]
    with pytest.raises(ConnectionError):
        runtime.invoke("Bear Researcher", spec, backend, value, {})
    with pytest.raises(ValueError, match="giới hạn"):
        runtime.invoke("Bear Researcher", spec, backend, value, {})
    assert backend.calls == 1


def test_backtest_has_no_deepseek_by_default():
    cfg = config()
    effective = backtest_config(cfg)
    assert effective["desktop_role_models"]["Bear Researcher"]["provider"] == "codex_cli"
    assert cfg["desktop_role_models"]["Bear Researcher"]["provider"] == "deepseek"


def test_explicit_backtest_opt_in_is_preserved():
    cfg = config()
    cfg["desktop_budget"]["deepseek_in_backtest"] = True
    assert backtest_config(cfg) is cfg


def test_budget_is_active_even_when_usage_and_vn_are_disabled(tmp_path, monkeypatch):
    from desktop import integration

    cfg = config(tmp_path)
    cfg.update(desktop_usage={"enabled": False}, desktop_vietnam={"enabled": False})

    class Graph:
        config = cfg

    monkeypatch.setattr(integration, "apply_role_models", lambda *args, **kw: None)
    _, runtime, _, _ = integration.prepare(
        Graph(), "AAPL", "2026-10-30", None, cfg, [], lambda *args: None
    )
    assert runtime is not None
    runtime.paid_budget.reserve("deepseek")
    with pytest.raises(ValueError):
        runtime.paid_budget.reserve("deepseek")
