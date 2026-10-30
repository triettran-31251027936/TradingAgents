"""Sector exclusions are explicit; they must never disguise unavailable bank inputs."""

BANK_INDUSTRIAL_ONLY = frozenset(
    {
        "revenue",
        "revenue_ttm",
        "revenue_yoy",
        "gross_profit",
        "gross_margin",
        "operating_profit",
        "net_margin_ttm",
        "current_assets",
        "current_liabilities",
        "current_ratio",
        "short_term_borrowings",
        "long_term_borrowings",
        "borrowings_to_equity",
        "fcf_ttm",
        "cash_conversion_ttm",
        "cash_less_borrowings",
        "cash_and_equivalents",
    }
)


def apply_bank(metrics, bank_proof):
    if not bank_proof:
        return
    for name in BANK_INDUSTRIAL_ONLY:
        if name in metrics:
            metrics[name] = {
                **metrics[name],
                "value": None,
                "status": "not_applicable",
                "formula": "Not applicable to this banking statement presentation. Use bank-specific income, funding, signed provisions and liquidity notes; deposit-driven CFO is not industrial FCF. Original definition: "
                + metrics[name]["formula"],
            }


def missing(metrics):
    return [
        k for k, m in metrics.items() if m["value"] is None and m.get("status") != "not_applicable"
    ]
