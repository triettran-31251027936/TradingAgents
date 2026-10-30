"""Valuation arithmetic with observable-date and consolidated-scope checks.

Provider ratios are recorded for comparison, never silently treated as our own
calculations or as a point-in-time historical archive.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from desktop.financial_metrics import number, period, ttm

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def parent_equity(book, target, field):
    """Identify total consolidated equity through A-L=E before subtracting NCI."""
    section = book["balance"]
    rows = [r for r in section.get("quarters", []) if period(r) == target]
    if len(rows) != 1:
        return None, "Missing or duplicate balance-sheet period"
    row = rows[0]
    titles = [
        ("Total Assets",),
        ("Liabilities", "TOTAL LIABILITIES"),
        ("Owner's Equity",),
        ("Minority interests", "Minority interest"),
    ]
    values = [number(row.get(field(book, "balance", *aliases))) for aliases in titles]
    if any(v is None for v in values):
        return None, "Assets, liabilities, equity or non-controlling interests unavailable"
    assets, liabilities, equity, nci = values
    if assets <= 0 or liabilities < 0 or equity <= 0 or equity - nci <= 0:
        return None, "Non-positive equity or invalid assets/liabilities; ratio withheld"
    if abs(assets - liabilities - equity) > max(1, abs(assets) * 0.00001):
        return None, "Assets - liabilities does not reconcile to total equity"
    return equity - nci, f"{target[0]}-Q{target[1]} public:{str(row.get('publicDate', ''))[:10]}"


def extend(book, prices, raw, asof, observed_at, add, field, *, company_profile=None):
    profit, reconciliation, window = ttm(
        book["income"], field(book, "income", "Attributable to parent company")
    )
    end = window[-1] if window else None
    beginning = (end[0] - 1, end[1]) if end else None
    end_equity, end_note = (
        parent_equity(book, end, field) if end else (None, "No contiguous TTM window")
    )
    begin_equity, begin_note = (
        parent_equity(book, beginning, field) if beginning else (None, "No contiguous TTM window")
    )
    source = "Vietcap published statements; Python arithmetic"
    stamp = f"{begin_note}; {end_note}"
    add(
        "equity_parent_end_ttm",
        end_equity,
        "VND",
        "Consolidated equity minus non-controlling interests at TTM end; requires A-L=E reconciliation",
        source,
        end_note,
    )
    add(
        "equity_parent_begin_ttm",
        begin_equity,
        "VND",
        "Consolidated equity minus non-controlling interests one year before TTM end; requires A-L=E reconciliation",
        source,
        begin_note,
    )
    roe = (
        profit / ((begin_equity + end_equity) / 2)
        if profit is not None and begin_equity is not None and end_equity is not None
        else None
    )
    add(
        "roe_ttm",
        roe,
        "fraction",
        "Reconciled parent-attributable profit TTM / mean(parent-attributable equity at TTM beginning and end); two-point average, not daily weighted equity",
        source,
        stamp,
    )
    proof = {
        "profit_reconciliation": reconciliation,
        "ttm_window": window,
        "begin_equity": begin_note,
        "end_equity": end_note,
    }
    observed = datetime.fromisoformat(observed_at) if isinstance(observed_at, str) else observed_at
    if observed.tzinfo is None:
        raise ValueError("Valuation retrieval time must have a timezone")
    items = raw.get("data", []) if isinstance(raw, dict) else []
    reason = "Current observation only; not a historical valuation archive"
    chosen = None
    candidates = []
    if isinstance(items, list):
        for row in items:
            try:
                key = (int(row["yearReport"]), int(row["quarter"]))
            except (KeyError, ValueError, TypeError):
                continue
            if 1 <= key[1] <= 4:
                candidates.append((key, row))
    if end and observed.astimezone(TZ).date().isoformat() == asof and candidates:
        latest = max(key for key, _ in candidates)
        matched = [row for key, row in candidates if key == latest]
        if latest == end and len(matched) == 1:
            chosen = matched[0]
        else:
            reason = "Latest provider statistics period differs from published TTM statements or is duplicated"
    profile = company_profile or {}
    use_profile = (
        profile.get("status") == "available" and observed.astimezone(TZ).date().isoformat() == asof
    )
    market = profile.get("market", {}) if use_profile else chosen
    shares = number(market.get("numberOfSharesMktCap")) if market else None
    cap = number(market.get("marketCap")) if market else None
    close = number(prices[-1]["close"]) if prices else None
    aligned = (
        shares is not None
        and shares > 0
        and shares.is_integer()
        and cap is not None
        and cap > 0
        and close is not None
        and close > 0
    )
    aligned = bool(aligned and abs(cap / shares - close) <= max(0.000001, close * 0.00001))
    if use_profile:
        profile_price = number(market.get("currentPrice"))
        issued = number(market.get("issueShare"))
        aligned = bool(
            aligned
            and profile_price is not None
            and abs(profile_price - close) <= max(0.000001, close * 0.00001)
            and issued == shares
        )
    matching_balances = [r for r in book["balance"].get("quarters", []) if period(r) == end]
    latest_balance = matching_balances[0] if len(matching_balances) == 1 else {}
    capital_key = field(book, "balance", "Paid-in capital")
    section = book["balance"]
    metadata = {k.casefold(): v for k, v in section.get("field_metadata", {}).items()}
    labels = {k.casefold(): v.strip().casefold() for k, v in section["fields"].items()}

    def capital_parent(code):
        parent = str(code).casefold()
        if capital_key and parent == capital_key.casefold():
            return True
        # Banking capital has a different caption/code, but must be a child of
        # shareholder equity. A similarly named liability is not common equity.
        record = metadata.get(parent, {})
        grand = str(record.get("parent", "")).casefold()
        return labels.get(parent) == "capital" and labels.get(grand) == "owner's equity"

    preferred_fields = [
        key
        for key, description in section.get("field_metadata", {}).items()
        if description.get("titleEn", "").strip().casefold() == "preferred shares"
        and capital_parent(description.get("parent"))
        and latest_balance.get(key) is not None
    ]
    preferred = (
        number(latest_balance.get(preferred_fields[0])) if len(preferred_fields) == 1 else None
    )
    if market and not aligned:
        reason = "Market cap / stated shares does not match the canonical completed close; do not mix price bases"
    if market and preferred != 0:
        note = "Preferred share capital is non-zero or unknown; common-equity valuation withheld"
        reason = reason + "; " + note if not aligned else note
    market_verified = bool(market and aligned)
    verified = bool(market_verified and preferred == 0)
    proof.update(
        market_input_source="company_details" if use_profile else "statistics_financial",
        observed_at=observed.isoformat(),
        statistics_period=list(max(key for key, _ in candidates)) if candidates else None,
        statement_ttm_end=list(end) if end else None,
        marketcap_price_aligned=aligned,
        preferred_capital=preferred,
        preferred_field=preferred_fields[0] if len(preferred_fields) == 1 else None,
        status="verified_current_inputs"
        if verified
        else "market_verified_valuation_withheld"
        if market_verified
        else "withheld",
        reason=None if verified else reason,
    )
    market_source = (
        (
            "Vietcap company/details observed "
            if use_profile
            else "Vietcap statistics-financial observed "
        )
        + observed.isoformat()
        + "; matched to canonical close "
        + str(prices[-1]["date"] if prices else "?")
    )

    def emit(name, value, unit, formula, *, allow=None):
        allowed = verified if allow is None else allow
        add(
            name,
            value,
            unit,
            formula if allowed else formula + "; WITHHELD: " + reason,
            market_source,
            asof,
        )

    emit(
        "shares_marketcap_observed",
        shares if market_verified else None,
        "shares",
        "Provider share count used in observed market capitalization; not weighted-average EPS shares",
        allow=market_verified,
    )
    emit(
        "market_cap_at_canonical_close",
        cap if market_verified else None,
        "VND",
        "Observed market cap, only when cap/shares matches canonical completed close and current issuer inputs or a statistics period matching published statements",
        allow=market_verified,
    )
    emit(
        "pe_ttm",
        cap / profit if verified and profit is not None and profit > 0 else None,
        "ratio",
        "Market cap / positive reconciled parent-attributable profit TTM; not a sum of quarterly EPS",
    )
    emit(
        "pb",
        cap / end_equity if verified and end_equity is not None else None,
        "ratio",
        "Market cap / positive parent-attributable equity at published TTM end; preferred capital must be explicitly zero",
    )
    comparison_aligned = bool(
        chosen
        and number(chosen.get("numberOfSharesMktCap")) == shares
        and number(chosen.get("marketCap")) == cap
    )
    for name, key, unit in [
        ("provider_pe_ttm_observed", "pe", "ratio"),
        ("provider_pb_observed", "pb", "ratio"),
        ("provider_roe_ttm_observed", "roe", "fraction"),
    ]:
        add(
            name,
            number(chosen.get(key)) if verified and comparison_aligned else None,
            unit,
            "Provider-reported comparison only; methodology may differ from Python calculation; no historical availability guarantee"
            + (
                ""
                if verified and comparison_aligned
                else "; WITHHELD: provider statistics period or price basis not aligned"
            ),
            "Vietcap statistics-financial observed " + observed.isoformat(),
            asof,
        )
    return proof
