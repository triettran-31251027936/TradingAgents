"""The balanced preset must remain equal across the source, UI metadata and budget."""

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from desktop.budget import PaidBudget, constrain, validate_config
from desktop.model_routing import ROLE_NAMES
from desktop.profiles import BALANCED_BUDGET, DEEPSEEK_BALANCED_ROLES, balanced_routes
from tradingagents.default_config import DEFAULT_CONFIG


def balanced_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(
        llm_provider="codex_cli",
        quick_think_llm="gpt-6-luna",
        deep_think_llm="gpt-6.1-sol",
        desktop_role_models=balanced_routes(),
        desktop_budget=dict(BALANCED_BUDGET),
        max_tool_rounds=6,
    )
    return cfg


def test_balanced_three_providers_cover_every_original_role_once():
    routes = balanced_routes()
    assert set(routes) == set(ROLE_NAMES)
    assert Counter(r["provider"] for r in routes.values()) == {
        "codex_cli": 6,
        "deepseek": 6,
        "agy_cli": 1,
    }
    assert {
        role for role, s in routes.items() if s["provider"] == "deepseek"
    } == DEEPSEEK_BALANCED_ROLES
    assert routes["Portfolio Manager"] == {
        "provider": "codex_cli",
        "model": "gpt-6.1-sol",
        "effort": "high",
    }
    assert routes["Sentiment Analyst"]["model"] == "gemini-3.8-flash-high"
    # The live acceptance measured three API turns per analyst, versus one
    # per risk role. Split tool-heavy analysts too, while retaining 6/6 roles.
    assert routes["Fundamentals Analyst"]["provider"] == "codex_cli"
    assert routes["Neutral Analyst"]["provider"] == "deepseek"
    assert all(
        r["model"] == "deepseek-flash" for r in routes.values() if r["provider"] == "deepseek"
    )


def test_balanced_preset_is_not_blocked_by_retired_one_request_limit():
    cfg = balanced_config()
    validate_config(cfg)
    cfg["desktop_budget"]["deepseek_max_requests"] = 1
    with pytest.raises(ValueError, match="ngân sách"):
        validate_config(cfg)


def test_budget_allows_tool_loops_but_stops_after_explicit_cap():
    cfg = balanced_config()
    budget = PaidBudget(cfg)
    # Two analysts retain all six tool rounds and a final report. Add both
    # researchers, neutral risk and one Memory reflection.
    expected = 2 * (cfg["max_tool_rounds"] + 1) + 2 + 1 + 1
    assert expected == 18
    for _ in range(expected):
        budget.reserve("deepseek")
    with pytest.raises(ValueError):
        budget.reserve("deepseek")
    assert budget.summary()["deepseek_attempts"] == expected


def test_every_paid_role_is_capped_without_sdk_retry():
    cfg = balanced_config()
    for spec in cfg["desktop_role_models"].values():
        if spec["provider"] == "deepseek":
            kwargs = constrain(cfg, spec)
            assert kwargs["max_tokens"] == 2048
            assert kwargs["llm_max_retries"] == 0


def test_two_provider_preset_explicitly_accounts_for_odd_role_count():
    routes = balanced_routes(False)
    assert Counter(s["provider"] for s in routes.values()) == {"codex_cli": 7, "deepseek": 6}


def test_meta_exposes_same_balanced_preset_to_ui(monkeypatch, tmp_path):
    from desktop import bridge

    cfg = balanced_config()
    cfg["results_dir"] = str(tmp_path / "reports")
    monkeypatch.setattr(bridge, "config", lambda: cfg)
    meta = bridge.dispatch("meta", {})
    preset = meta["routing_presets"]["balanced_three"]
    assert preset["routes"] == balanced_routes()
    assert preset["budget"] == BALANCED_BUDGET
    assert meta["routing_presets"]["balanced_two"]["routes"] == balanced_routes(False)


def test_example_profile_reproduces_equal_allocation():
    example = json.loads(
        (Path(__file__).resolve().parents[1] / "config/desktop.example.json").read_text()
    )
    assert example["desktop_role_models"] == balanced_routes()
    assert example["desktop_budget"] == BALANCED_BUDGET
    validate_config({**DEFAULT_CONFIG, **example})
