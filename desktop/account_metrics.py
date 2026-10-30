"""Deterministic virtual-account sizing context, never an order recommendation."""

from __future__ import annotations

import math


def evidence(bundle, book):
    if not book:
        return {
            "status": "unavailable",
            "reason": "No virtual account supplied; do not assume cash or holdings.",
        }
    metrics = bundle["metrics"]
    ticker = bundle["ticker"]
    position = book["positions"].get(ticker, {})

    def metric(name):
        return metrics.get(name, {}).get("value")

    close = metric("close")
    nav = book["nav"]
    quantity = position.get("quantity", 0)
    rows = {}

    def add(name, value, unit, formula):
        rows[name] = {
            "value": value,
            "unit": unit,
            "formula": formula,
            "quote_date": bundle["prices"][-1]["date"],
        }

    value = quantity * close if close is not None else None
    add(
        "current_ticker_exposure",
        value,
        "VND",
        "held quantity × canonical completed close; not total portfolio exposure",
    )
    add(
        "current_ticker_weight",
        value / nav if value is not None and nav > 0 else None,
        "fraction",
        "ticker exposure / virtual account NAV",
    )
    var = metric("historical_var95_1d")
    cvar = metric("historical_cvar95_1d")
    add(
        "current_ticker_historical_var95_vnd",
        value * var if value is not None and var is not None else None,
        "VND/day",
        "ticker exposure × historical single-stock VaR95; not portfolio VaR, forecast or guaranteed loss bound",
    )
    add(
        "current_ticker_historical_cvar95_vnd",
        value * cvar if value is not None and cvar is not None else None,
        "VND/day",
        "ticker exposure × historical single-stock CVaR95; no diversification or market-impact model",
    )
    cap = (
        max(
            0,
            nav * book["max_position_fraction"]
            - (value or 0)
            - book.get("reserved_buy_value", {}).get(ticker, 0),
        )
        if value is not None
        else None
    )
    add(
        "remaining_position_budget",
        cap,
        "VND",
        "NAV × configured position cap - current canonical exposure - approved pending buy notional",
    )
    affordable = None
    if close is not None and close > 0 and cap is not None:
        affordable = max(
            0,
            math.floor(
                min(book["available_cash"] / (close * (1 + book["commission"])), cap / close) / 100
            )
            * 100,
        )
    add(
        "max_buy_quantity_at_reference",
        affordable,
        "shares",
        "floor(min(cash incl commission capacity, position-cap capacity) / 100) ×100; uses reference close only, NOT an executable recommendation or future fill price",
    )
    add(
        "settled_sell_quantity",
        position.get("available_quantity", 0),
        "shares",
        "Only settled/unreserved holdings supplied by the virtual account",
    )
    return {
        "status": "available",
        "mode": book["mode"],
        "account_clock": book["clock"],
        "account_fingerprint": book["fingerprint"],
        "metrics": rows,
        "limitations": "Reference calculations only. Any proposed order must be checked again at its actual limit, fees, current book and human approval.",
    }
