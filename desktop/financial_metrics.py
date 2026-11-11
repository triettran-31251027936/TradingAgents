"""Reconciled financial arithmetic; no LLM estimates or fabricated valuation inputs."""

from __future__ import annotations

import math


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def period(row):
    return int(row["yearReport"]), int(row.get("lengthReport", 5))


def reconcile(section, key):
    """Infer flow representation only when complete quarters reconcile to FY.

    An arithmetic match is evidence of representation, not an audit of the
    financial statements or a guarantee of unchanged reporting methodology.
    """
    quarters = {period(r): r for r in section.get("quarters", [])}
    annual = {int(r["yearReport"]): r for r in section.get("years", [])}
    checks = []
    if not key:
        return {"basis": "unverified", "checks": [], "reason": "Provider field unavailable"}
    for year in sorted(annual, reverse=True):
        fy = number(annual[year].get(key))
        values = [number(quarters.get((year, q), {}).get(key)) for q in range(1, 5)]
        if fy is None or any(v is None for v in values):
            continue
        tolerance = max(1, abs(fy) * 0.005)
        discrete = abs(sum(values) - fy) <= tolerance
        ytd = abs(values[-1] - fy) <= tolerance
        basis = (
            "discrete" if discrete and not ytd else "ytd" if ytd and not discrete else "ambiguous"
        )
        checks.append(
            {
                "year": year,
                "annual": fy,
                "quarter_sum": sum(values),
                "q4": values[-1],
                "tolerance": tolerance,
                "basis": basis,
            }
        )
        if len(checks) == 2:
            break
    bases = {r["basis"] for r in checks}
    verified = bool(checks) and len(bases) == 1 and "ambiguous" not in bases
    return {
        "basis": checks[0]["basis"] if verified else "unverified",
        "checks": checks,
        "reason": "Complete quarters reconcile to published FY within0.5%; empirical basis, not an audited or vintage guarantee."
        if verified
        else "Missing, ambiguous or conflicting quarter/FY reconciliation; do not sum.",
    }


def ttm(section, key):
    proof = reconcile(section, key)
    if proof["basis"] == "unverified":
        return None, proof, []
    rows = {period(r): r for r in section.get("quarters", []) if 1 <= period(r)[1] <= 4}
    if not rows:
        return None, proof, []
    end = max(rows)
    latest_check = max((r["year"] for r in proof["checks"]), default=0)
    if latest_check < end[0] - 2:
        proof = {
            **proof,
            "basis": "unverified",
            "reason": "Quarter/FY reconciliation is too old for this reporting window.",
        }
        return None, proof, []
    serial = end[0] * 4 + end[1] - 1
    keys = [((n // 4), n % 4 + 1) for n in range(serial - 3, serial + 1)]
    values = []
    for year, q in keys:
        row = rows.get((year, q))
        value = number(row.get(key)) if row else None
        if value is None:
            return None, proof, keys
        if proof["basis"] == "ytd" and q > 1:
            previous = rows.get((year, q - 1))
            prior = number(previous.get(key)) if previous else None
            if prior is None:
                return None, proof, keys
            value -= prior
        values.append(value)
    return sum(values), proof, keys


def reconciled_assets(book, field, target):
    rows = [row for row in book["balance"]["quarters"] if period(row) == target]
    if len(rows) != 1:
        return None, "Missing or duplicate balance-sheet period"
    row = rows[0]
    assets, liabilities, equity = (
        number(row.get(field(book, "balance", *titles)))
        for titles in (("Total Assets",), ("Liabilities", "TOTAL LIABILITIES"), ("Owner's Equity",))
    )
    if assets is None or liabilities is None or equity is None or assets <= 0 or liabilities < 0:
        return None, "Missing or invalid balance-sheet totals"
    if abs(assets - liabilities - equity) > max(1, assets * 0.00001):
        return None, "Assets do not reconcile to liabilities plus equity"
    return assets, f"{target[0]}-Q{target[1]} public:{str(row.get('publicDate', ''))[:10]}"


def extend(book, add, field):
    def key(kind, *titles):
        return field(book, kind, *titles)

    def last(kind):
        return book[kind]["quarters"][-1] if book[kind]["quarters"] else {}

    latest = last("income")
    stamp = f"{latest.get('yearReport', '?')}-Q{latest.get('lengthReport', '?')} public:{str(latest.get('publicDate', ''))[:10]}"
    source = "Vietcap published statements; Python arithmetic"
    proof = {}
    values = {}
    windows = {}
    for name, kind, titles in [
        ("revenue_ttm", "income", ("Net sales",)),
        ("net_profit_ttm", "income", ("Net profit/(loss) after tax",)),
        ("net_profit_parent_ttm", "income", ("Attributable to parent company",)),
        (
            "operating_cashflow_ttm",
            "cash",
            (
                "Net cash inflows/(outflows) from operating activities",
                "Net cash from operating activities",
            ),
        ),
        ("capex_signed_ttm", "cash", ("Purchases of fixed assets and other long term assets",)),
        ("pretax_profit_ttm", "income", ("Net accounting profit/(loss) before tax",)),
        ("interest_expense_signed_ttm", "income", ("Interest expenses",)),
        ("interest_paid_signed_ttm", "cash", ("Interest paid",)),
        ("depreciation_amortization_ttm", "cash", ("Depreciation and amortization",)),
    ]:
        value, check, window = ttm(book[kind], key(kind, *titles))
        proof[name] = check
        values[name] = value
        windows[name] = window
        dated = last(kind)
        field_stamp = f"{dated.get('yearReport', '?')}-Q{dated.get('lengthReport', '?')} public:{str(dated.get('publicDate', ''))[:10]}"
        add(
            name,
            value,
            "VND",
            "TTM of four contiguous quarters; "
            + check["basis"]
            + " flow representation from quarter/FY reconciliation; "
            + check["reason"],
            source,
            field_stamp,
        )

    def ratio(a, b):
        return a / b if a is not None and b is not None and b > 0 else None

    cfo = values["operating_cashflow_ttm"]
    capex = values["capex_signed_ttm"]
    aligned = windows["operating_cashflow_ttm"] == windows["capex_signed_ttm"]
    fcf = cfo + capex if cfo is not None and capex is not None and capex <= 0 and aligned else None
    add(
        "fcf_ttm",
        fcf,
        "VND",
        "CFO TTM + signed purchases of fixed/long-term assets; requires same quarters, reconciled basis and non-positive capex. This capex definition may include other long-term assets.",
        source,
        f"{last('cash').get('yearReport', '?')}-Q{last('cash').get('lengthReport', '?')} public:{str(last('cash').get('publicDate', ''))[:10]}",
    )
    same = windows["net_profit_ttm"] == windows["revenue_ttm"]
    add(
        "net_margin_ttm",
        ratio(values["net_profit_ttm"], values["revenue_ttm"]) if same else None,
        "fraction",
        "Total after-tax profit TTM / revenue TTM, same four quarters",
        source,
        stamp,
    )
    add(
        "cash_conversion_ttm",
        ratio(cfo, values["net_profit_ttm"])
        if windows["operating_cashflow_ttm"] == windows["net_profit_ttm"]
        else None,
        "ratio",
        "CFO TTM / positive total after-tax profit TTM, same quarters",
        source,
        stamp,
    )
    profit = values["net_profit_ttm"]
    window = windows["net_profit_ttm"]
    roa = None
    roa_stamp = stamp
    if profit is not None and window:
        year, quarter = window[-1]
        opening, opening_note = reconciled_assets(book, field, (year - 1, quarter))
        closing, closing_note = reconciled_assets(book, field, (year, quarter))
        roa_stamp = f"Income: {stamp}; opening balance: {opening_note}; closing balance: {closing_note}"
        if opening is not None and closing is not None:
            roa = ratio(profit, (opening + closing) / 2)
    add(
        "roa_ttm",
        roa,
        "fraction",
        "Consolidated after-tax profit for four contiguous quarters / average of reconciled total assets "
        "at the start and end of the same window.",
        source,
        roa_stamp,
    )
    bal = last("balance")
    bdate = f"{bal.get('yearReport', '?')}-Q{bal.get('lengthReport', '?')} public:{str(bal.get('publicDate', ''))[:10]}"
    cash = number(bal.get(key("balance", "Cash and cash equivalents")))
    short = number(bal.get(key("balance", "Short-term borrowings")))
    long = number(bal.get(key("balance", "Long-term borrowings")))
    add(
        "cash_and_equivalents",
        cash,
        "VND",
        "Latest published balance sheet: cash and cash equivalents",
        source,
        bdate,
    )
    gross = short + long if short is not None and long is not None and min(short, long) >= 0 else None
    add(
        "gross_borrowings",
        gross,
        "VND",
        "Short-term plus long-term borrowings in the same balance sheet. This is a reported balance, not a contractual maturity schedule.",
        source,
        bdate,
    )
    add(
        "cash_to_short_borrowings",
        ratio(cash, short) if cash is not None and cash >= 0 else None,
        "ratio",
        "Cash and equivalents / positive short-term borrowings. Other current liabilities and access restrictions require separate review.",
        source,
        bdate,
    )
    add(
        "cash_less_borrowings",
        cash - short - long if all(v is not None for v in (cash, short, long)) else None,
        "VND",
        "Cash and equivalents minus reported short-term and long-term borrowings. "
        "No separate adjustment for leases, financial investments or restricted cash.",
        source,
        bdate,
    )
    eps = number(latest.get(key("income", "EPS basic (VND)")))
    add(
        "eps_basic_reported_period",
        eps,
        "VND/share",
        "Provider EPS for latest reported period; NOT EPS TTM; do not sum quarterly EPS with different weighted shares",
        source,
        stamp,
    )
    pretax = values["pretax_profit_ttm"]
    interest = values["interest_expense_signed_ttm"]
    depreciation = values["depreciation_amortization_ttm"]
    same_income = windows["pretax_profit_ttm"] == windows["interest_expense_signed_ttm"]
    earnings_before_interest = (
        pretax - interest
        if same_income and pretax is not None and interest is not None and interest <= 0
        else None
    )
    add(
        "pretax_plus_interest_coverage_ttm",
        ratio(earnings_before_interest, -interest) if interest is not None else None,
        "ratio",
        "(Pretax profit TTM minus signed interest expense TTM) / positive interest expense. Same quarters; includes non-operating gains and does not measure cash debt service.",
        source,
        stamp,
    )
    same_cash = windows["pretax_profit_ttm"] == windows["depreciation_amortization_ttm"]
    add(
        "ebitda_proxy_ttm",
        earnings_before_interest + depreciation
        if same_cash and earnings_before_interest is not None and depreciation is not None and depreciation >= 0
        else None,
        "VND",
        "Pretax profit minus signed interest expense plus cash-flow statement depreciation and amortization, on the same four quarters. Includes non-operating items; may differ from provider EBITDA.",
        source,
        stamp,
    )
    # Price/share-based valuation is added separately with observable-date checks.
    return proof
