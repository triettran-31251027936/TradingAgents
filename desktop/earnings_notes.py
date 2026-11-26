"""Issuer EPS notes with explicit period, numerator and restated share basis."""

from desktop.debt_notes import (
    VHM_Q3_2026_AVAILABLE, VHM_Q3_2026_DISCLOSURE, VHM_Q3_2026_SHA256, VHM_Q3_2026_URL,
)
from desktop.financial_metrics import number, period, reconcile


def load(ticker, asof, book, field):
    if ticker != "VHM" or asof < VHM_Q3_2026_AVAILABLE:
        return {}
    income = book["income"]
    rows = income.get("quarters", [])
    if not rows or max(period(row) for row in rows) != (2026, 3):
        return {}
    key = field(book, "income", "Attributable to parent company")
    if reconcile(income, key)["basis"] != "discrete":
        return {}
    selected = [row for row in rows if period(row) in ((2026, 1), (2026, 2), (2026, 3))]
    if len(selected) != 3 or len({period(row) for row in selected}) != 3:
        return {}
    profits = [number(row.get(key)) for row in selected]
    if any(value is None for value in profits) or abs(sum(profits) - 71_381_139_000_000) > 3_000_000:
        return {}
    earnings = 71_381_139_000_000 - 2_191_485_000_000 - 224_816_000_000
    shares = 8_214_824_008
    if round(earnings / shares) != 8395:
        raise ValueError("VHM Note 33 EPS arithmetic does not reconcile")
    return {
        "status": "available", "period_start": "2026-01-01", "period_end": "2026-09-30",
        "available_by": VHM_Q3_2026_AVAILABLE, "note": 33, "pdf_page": 70,
        "quarterly_parent_profit_sum_vnd": sum(profits),
        "reconciliation_tolerance_vnd": 3_000_000,
        "parent_profit_vnd": 71_381_139_000_000,
        "preferred_dividend_adjustment_vnd": -2_191_485_000_000,
        "preferred_redemption_adjustment_vnd": -224_816_000_000,
        "earnings_for_eps_vnd": earnings, "weighted_average_common_shares": shares,
        "basic_eps_vnd": 8395, "diluted_eps_vnd": 8395,
        "comparison_2025_restated_eps_vnd": 1751,
        "scope": "Issuer Note 33 for nine months to 30 September 2026. EPS numerator includes "
                 "preferred-share adjustments at a subsidiary. Weighted shares and the prior-year "
                 "comparison were restated for the stock dividend. These are nine-month EPS values; "
                 "TTM EPS requires reconciled adjusted earnings and share bases for the full year.",
        "source": {"name": "Thuyết minh EPS hợp nhất VHM · HOSE", "url": VHM_Q3_2026_URL,
                   "sha256": VHM_Q3_2026_SHA256, "published": VHM_Q3_2026_AVAILABLE,
                   "disclosure_url": VHM_Q3_2026_DISCLOSURE, "status": "available"},
    }


def metrics(notes):
    if not notes:
        return {}
    return {
        "issuer_eps_9m_2026": {
            "value": notes["basic_eps_vnd"], "unit": "VND/share",
            "asof": notes["period_end"], "published": notes["available_by"],
            "formula": "Issuer Note 33 basic EPS for nine months, after subsidiary preferred-share "
                       "adjustments and restated weighted-average common shares. Not TTM EPS.",
            "source": notes["source"]["url"], "status": "available",
        }
    }
