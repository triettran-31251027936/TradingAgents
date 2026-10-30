"""Explicit role allocations shared by reproducible desktop profiles."""

from __future__ import annotations

DEEPSEEK_BALANCED_ROLES = frozenset(
    {
        "Market Analyst",
        "News Analyst",
        "Neutral Analyst",
        "Bull Researcher",
        "Bear Researcher",
        "Memory Log",
    }
)
BALANCED_BUDGET = {
    "enabled": True,
    "deepseek_max_requests": 18,
    "deepseek_max_output_tokens": 2048,
    "deepseek_in_backtest": True,
}


def balanced_routes(include_antigravity=True, role_names=None):
    if role_names is None:
        from desktop.model_routing import ROLE_NAMES

        role_names = ROLE_NAMES
    routes = {}
    for role in role_names:
        if role in DEEPSEEK_BALANCED_ROLES:
            routes[role] = {"provider": "deepseek", "model": "deepseek-flash", "max_tokens": 2048}
        elif role == "Sentiment Analyst" and include_antigravity:
            routes[role] = {"provider": "agy_cli", "model": "gemini-3.8-flash-high"}
        else:
            routes[role] = {
                "provider": "codex_cli",
                "model": "gpt-6.1-sol",
                "effort": "high"
                if role == "Portfolio Manager"
                else "medium"
                if role in ("Research Manager", "Trader", "Conservative Analyst")
                else "low",
            }
    return routes
