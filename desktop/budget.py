"""Explicit paid-API limits; Codex/agy subscriptions are not dollar estimates."""

from __future__ import annotations

import copy
import threading

DEFAULT = {
    "enabled": True,
    "deepseek_max_requests": 1,
    "deepseek_max_output_tokens": 1024,
    "deepseek_in_backtest": False,
}


def validate_config(cfg):
    budget = cfg.get("desktop_budget", {})
    if not isinstance(budget, dict) or set(budget) - set(DEFAULT):
        raise ValueError("Cấu hình ngân sách DeepSeek không hợp lệ.")
    for key in ("enabled", "deepseek_in_backtest"):
        if key in budget and type(budget[key]) is not bool:
            raise ValueError(key + " phải là boolean.")
    for key, minimum in [("deepseek_max_requests", 0), ("deepseek_max_output_tokens", 1)]:
        if key in budget and (type(budget[key]) is not int or budget[key] < minimum):
            raise ValueError(key + " không hợp lệ.")
    if not budget.get("enabled"):
        return
    # Catch paid-heavy presets before they are saved. Runtime still enforces
    # the request cap, including extra tool rounds and failed attempts.
    routes = cfg.get("desktop_role_models", {})
    from desktop.model_routing import ROLE_NAMES

    paid = []
    for role in ROLE_NAMES:
        tier = "deep" if role in ("Research Manager", "Portfolio Manager") else "quick"
        provider = (
            routes.get(role, {}).get("provider")
            or cfg.get(tier + "_think_provider")
            or cfg.get("llm_provider")
        )
        if str(provider).lower() == "deepseek":
            paid.append(role)
    minimum = sum(
        cfg.get("max_debate_rounds", 1)
        if role in ("Bull Researcher", "Bear Researcher")
        else cfg.get("max_risk_discuss_rounds", 1)
        if role in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst")
        else 1
        for role in paid
    )
    if minimum > budget.get("deepseek_max_requests", 1):
        raise ValueError(
            "Phân công DeepSeek vượt ngân sách request. Dùng Codex cho các vai trò còn lại hoặc chủ động tăng giới hạn trong cấu hình ngân sách."
        )


def constrain(cfg, spec):
    result = {**cfg}
    budget = cfg.get("desktop_budget", {})
    if spec["provider"].lower() == "deepseek" and budget.get("enabled"):
        cap = budget.get("deepseek_max_output_tokens", 1024)
        result["max_tokens"] = min(spec.get("max_tokens") or cfg.get("max_tokens") or cap, cap)
        result["llm_max_retries"] = 0
    return result


class PaidBudget:
    def __init__(self, cfg):
        self.options = cfg.get("desktop_budget", {})
        self.calls = 0
        self.lock = threading.Lock()

    def reserve(self, provider):
        if str(provider).lower() != "deepseek" or not self.options.get("enabled"):
            return
        with self.lock:
            if self.calls >= self.options.get("deepseek_max_requests", 1):
                raise ValueError(
                    "Đã chạm giới hạn request DeepSeek của lượt phân tích. Không gọi API thêm; chuyển vai trò sang Codex để tiếp tục."
                )
            self.calls += 1

    def summary(self):
        return {
            "enabled": self.options.get("enabled", False),
            "deepseek_attempts": self.calls,
            "limits": self.options,
            "note": "Request/output-token caps, not a verified USD balance. Failed attempts count. No automatic paid fallback.",
        }


def backtest_config(cfg):
    budget = cfg.get("desktop_budget", {})
    if not budget.get("enabled") or budget.get("deepseek_in_backtest", False):
        return cfg
    result = copy.deepcopy(cfg)
    for spec in result.get("desktop_role_models", {}).values():
        if spec["provider"].lower() == "deepseek":
            spec.clear()
            spec.update(provider="codex_cli", model="gpt-6.1-sol", effort="low")
    for tier, model in [("quick", "gpt-6-luna"), ("deep", "gpt-6.1-sol")]:
        if (result.get(tier + "_think_provider") or result.get("llm_provider")) == "deepseek":
            result[tier + "_think_provider"] = "codex_cli"
            result[tier + "_think_llm"] = model
            result[tier + "_think_backend_url"] = None
    return result
