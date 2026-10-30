"""Bank-specific statement arithmetic; observed provider ratios stay separate."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from desktop.financial_metrics import number, period, ttm

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
STOCKS = {
    "bank_customer_loans_gross": "Loans and advances to customers",
    "bank_customer_loans_net": "Loans and advances to customers, net",
    "bank_customer_loan_allowance_signed": "Less: Provision for losses on loans and advances to customers",
    "bank_customer_deposits": "Deposits from customers",
    "bank_interbank_placements_loans": "Placements with and loans to other credit institutions",
    "bank_sbv_balances": "Balances with the SBV",
    "bank_valuable_papers_issued": "Convertible bonds/CDs and other valuable papers issued",
}
FLOWS = {
    "bank_net_interest_income_ttm": "Net Interest Income",
    "bank_interest_income_ttm": "Interest and Similar Income",
    "bank_interest_expense_signed_ttm": "Interest and Similar Expenses",
    "bank_net_fee_income_ttm": "Net Fee and Commission Income",
    "bank_operating_income_ttm": "Total Operating Income",
    "bank_operating_expense_signed_ttm": "General and Admin Expenses",
    "bank_preprovision_profit_ttm": "Net Operating Profit Before Allowance for Credit Loss",
    "bank_credit_provision_signed_ttm": "Provision for Credit Losses",
}
PROVIDER = {
    "bank_provider_nim_observed": "netInterestMargin",
    "bank_provider_casa_observed": "casaRatio",
    "bank_provider_npl_observed": "npl",
    "bank_provider_ldr_observed": "ldrLoanDepositRatio",
    "bank_provider_cost_income_signed_observed": "costToIncome",
    "bank_provider_npl_coverage_signed_observed": "loansLossReservesToNPLs",
    "bank_provider_loan_allowance_ratio_observed": "loansLossReserveToLoans",
}


def extend(book, add, *, valuation_raw=None, asof, observed_at):
    balance = book["balance"].get("quarters", [])
    income = book["income"].get("quarters", [])
    if not balance or not income:
        return {}
    last = max(balance, key=period)
    end = period(last)
    latest_income = max(income, key=period)

    def key(kind, title):
        keys = [
            k
            for k, label in book[kind]["fields"].items()
            if label.strip().casefold() == title.casefold()
        ]
        return keys[0] if len(keys) == 1 else None

    nii = key("income", FLOWS["bank_net_interest_income_ttm"])
    dep = key("balance", STOCKS["bank_customer_deposits"])
    if (
        not nii
        or not dep
        or number(latest_income.get(nii)) is None
        or number(last.get(dep)) is None
    ):
        return {}
    stamp = f"{end[0]}-Q{end[1]} public:{str(last.get('publicDate', ''))[:10]}"
    source = "Vietcap published banking statements; Python arithmetic"
    proof = {
        "sector": "bank",
        "balance_period": list(end),
        "flows": {},
        "scope": "Accounting balance-sheet ratios are not SBV regulatory ratios; provider NIM/CASA/NPL are observed comparison data, not independently reconstructed from notes.",
    }
    ambiguous_balance = sum(period(r) == end for r in balance) != 1
    proof["ambiguous_balance_period"] = ambiguous_balance
    stocks = {}
    for name, title in STOCKS.items():
        k = key("balance", title)
        value = number(last.get(k)) if k and not ambiguous_balance else None
        stocks[name] = value
        add(
            name,
            value,
            "VND",
            f"Provider field {k}: {title}; published period-end stock",
            source,
            stamp,
        )
    gross = stocks["bank_customer_loans_gross"]
    net = stocks["bank_customer_loans_net"]
    allowance = stocks["bank_customer_loan_allowance_signed"]
    reconcile = (
        all(v is not None for v in (gross, net, allowance))
        and gross > 0
        and allowance <= 0
        and abs(gross + allowance - net) <= max(1, gross * 1e-9)
    )
    proof["loan_net_reconciliation"] = {
        "verified": reconcile,
        "gross": gross,
        "allowance_signed": allowance,
        "net": net,
        "formula": "gross + non-positive allowance = net within max(1 VND,1e-9 gross)",
    }
    deposits = stocks["bank_customer_deposits"]
    ratio = (
        gross / deposits
        if gross is not None and gross > 0 and deposits is not None and deposits > 0
        else None
    )
    add(
        "bank_accounting_loan_deposit_ratio",
        ratio,
        "fraction",
        "Gross customer loans / customer deposits at same period-end; excludes interbank and issued papers; NOT SBV regulatory LDR or a test against regulatory limits",
        source,
        stamp,
    )
    add(
        "bank_loan_allowance_ratio",
        -allowance / gross if reconcile else None,
        "fraction",
        "Negative loan allowance / gross customer loans; requires gross + signed allowance = net; not NPL ratio or NPL coverage",
        source,
        stamp,
    )
    values, windows = {}, {}
    for name, title in FLOWS.items():
        k = key("income", title)
        value, check, window = ttm(book["income"], k)
        periods = [period(r) for r in income]
        if len(periods) != len(set(periods)):
            value = None
            check = {
                **check,
                "basis": "unverified",
                "reason": "Duplicate income statement periods; do not select a row or sum",
            }
        proof["flows"][name] = check
        values[name], windows[name] = value, window
        dated = f"{period(latest_income)[0]}-Q{period(latest_income)[1]} public:{str(latest_income.get('publicDate', ''))[:10]}"
        add(
            name,
            value,
            "VND",
            "TTM four contiguous quarters; quarter/FY reconciliation basis "
            + check["basis"]
            + "; "
            + title
            + "; preserves accounting sign. "
            + check["reason"],
            source,
            dated,
        )

    def same(*names):
        return (
            all(values[n] is not None for n in names)
            and len({tuple(windows[n]) for n in names}) == 1
        )

    toi, opex, nii_name = (
        "bank_operating_income_ttm",
        "bank_operating_expense_signed_ttm",
        "bank_net_interest_income_ttm",
    )
    cir = (
        -values[opex] / values[toi]
        if same(toi, opex) and values[toi] > 0 and values[opex] <= 0
        else None
    )
    add(
        "bank_cost_income_ratio_ttm",
        cir,
        "fraction",
        "-Signed operating expense TTM / operating income TTM; requires aligned reconciled flows and non-positive expense; accounting CIR, not an adjusted issuer measure",
        source,
        dated,
    )
    noninterest = values[toi] - values[nii_name] if same(toi, nii_name) else None
    add(
        "bank_noninterest_income_ttm",
        noninterest,
        "VND",
        "Operating income TTM - net interest income TTM; same reconciled window; can be negative",
        source,
        dated,
    )

    candidates = []
    raw = (valuation_raw or {}).get("data") or []
    if isinstance(raw, list):
        for row in raw:
            try:
                if (int(row["yearReport"]), int(row["quarter"])) == end:
                    candidates.append(row)
            except (KeyError, TypeError, ValueError):
                pass
    observed = (
        observed_at if isinstance(observed_at, datetime) else datetime.fromisoformat(observed_at)
    )
    current = observed.tzinfo is not None and observed.astimezone(TZ).date().isoformat() == asof
    chosen = (
        candidates[0]
        if current
        and not ambiguous_balance
        and len(candidates) == 1
        and period(latest_income) == end
        else {}
    )
    proof["provider_comparison"] = {
        "status": "observed_unverified_methodology" if chosen else "withheld",
        "observed_at": observed.isoformat(),
        "period": list(end),
        "matching_rows": len(candidates),
        "reason": "Current observation only, same published balance/income period, unique provider row; provider lookback, adjustments and note denominators not independently verified. Signed ratios preserved; no silent abs/percentage conversion.",
    }
    for name, k in PROVIDER.items():
        add(
            name,
            number(chosen.get(k)) if chosen else None,
            "fraction",
            f"Provider {k} at {end[0]}-Q{end[1]}, observed {observed.isoformat()}; definition/lookback not independently verified; signed source ratio, not canonical reconstructed ratio",
            "Vietcap statistics-financial current observation",
            stamp,
        )
    return proof
