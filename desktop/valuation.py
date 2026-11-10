"""Valuation arithmetic with observable-date and consolidated-scope checks.

Provider ratios are recorded for comparison, never silently treated as our own
calculations or as a point-in-time historical archive.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

from desktop.financial_metrics import number, period, ttm

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def reconcile_registered_shares(book, registry, profile, target, asof, preferred):
    """Reconcile a current registry count to broker counts and reported equity.

    Match registered shares to published capital and zero reported treasury
    and preferred capital. A sole later stock dividend requires a dated VSDC
    notice and the corresponding issuer disclosure. The reported share basis
    cannot establish whether unreported post-statement buybacks occurred.
    """
    proof = {
        "verified": False,
        "scope": "Latest reported common-share basis cross-checked with current VSDC registration and broker share counts; not a guarantee against undisclosed post-statement buybacks.",
    }
    try:
        if (
            not registry
            or registry.get("status") != "available"
            or not registry.get("history_reconciled")
        ):
            raise ValueError("No available reconciled VSDC registration snapshot")
        historical = registry.get("source_basis") == "dated_registration_notice"
        count, par = registry["registered_shares"], registry["par_value_vnd"]
        if type(count) is not int or type(par) is not int or count <= 0 or par <= 0:
            raise ValueError("Invalid registry count/par")
        if historical:
            if registry.get("analysis_date") != asof or registry["notice_published"][:10] > asof:
                raise ValueError("Dated registration evidence is outside the analysis cutoff")
            proof.update(
                source_basis="dated_registration_notice",
                notice_url=registry["notice_url"], notice_published=registry["notice_published"],
                scope="Dated VSDC balance notice and published charter reconciliation, retrieved later. Undisclosed post-statement buybacks and revisions are not ruled out.",
            )
        else:
            if registry.get("ticker") != profile.get("ticker") or profile.get("status") != "available":
                raise ValueError("Issuer identity mismatch")
            for record in (registry, profile):
                stamp = datetime.fromisoformat(record["observed_at"])
                if stamp.tzinfo is None or stamp.astimezone(TZ).date().isoformat() != asof:
                    raise ValueError("Share observations are not from the analysis date")
            market = profile.get("market", {})
            if (
                number(market.get("issueShare")) != count
                or number(market.get("numberOfSharesMktCap")) != count
            ):
                raise ValueError("VSDC count differs from current broker issued/market-cap share counts")
        if not target:
            raise ValueError("No reconciled statement TTM end")
        rows = [r for r in book["balance"].get("quarters", []) if period(r) == target]
        if len(rows) != 1:
            raise ValueError("Missing or duplicate statement period")
        month = target[1] * 3
        end = date(target[0], month, calendar.monthrange(target[0], month)[1]).isoformat()
        if end > asof:
            raise ValueError("Statement period is in the future")
        statement_count = count
        if registry["latest_registration_date"] > end:
            changes = [row for row in registry.get("registration_history", []) if row["date"] > end]
            event = registry.get("notice_registration_change")
            if (
                not event or changes != [event]
                or event["date"] != registry["latest_registration_date"]
                or type(event.get("change")) is not int or not 0 < event["change"] < count
                or event.get("reason", "").casefold() != "trả cổ tức bằng cổ phiếu"
            ):
                raise ValueError("Registration changed after the latest statement")
            issuer = registry.get("issuer_capital_change") or {}
            if (
                not issuer
                or not event["date"] <= registry["notice_published"][:10] <= asof
                or not end < issuer["effective_date"] <= issuer["published"] <= asof
                or issuer["before_shares"] != count - event["change"]
                or issuer["after_shares"] != count
                or issuer["par_value_vnd"] != par
            ):
                raise ValueError("No matching issuer capital change published by the analysis cutoff")
            statement_count -= event["change"]
            proof["stock_dividend_bridge"] = {
                "registration_date": event["date"],
                "statement_share_basis": statement_count,
                "stock_dividend_shares": event["change"],
                "post_dividend_share_basis": count,
                "issuer_capital_change": issuer,
                "scope": "Sole later VSDC registration matched to the stock-dividend notice and independently dated issuer capital change. Capitalized dividend increases share count, not total equity. P/B uses last reported parent equity without forecasting other changes.",
            }
        published = date.fromisoformat(str(rows[0].get("publicDate", ""))[:10]).isoformat()
        if not end <= published <= asof:
            raise ValueError("Statement publication unavailable by analysis date")
        section = book["balance"]
        labels = {k.casefold(): v.strip().casefold() for k, v in section["fields"].items()}
        metadata = {k.casefold(): v for k, v in section.get("field_metadata", {}).items()}

        def equity_field(*titles):
            keys = [k for k, v in section["fields"].items() if v.strip().casefold() in titles]
            if len(keys) != 1:
                return None
            key, seen = keys[0].casefold(), set()
            while key and key not in seen:
                seen.add(key)
                key = str(metadata.get(key, {}).get("parent", "")).casefold()
                if labels.get(key) == "owner's equity":
                    return keys[0]
            return None

        charter = equity_field("paid-in capital", "charter capital")
        treasury = equity_field("treasury shares", "treasury stocks")
        capital, treasury_value = number(rows[0].get(charter)), number(rows[0].get(treasury))
        expected = statement_count * par
        tolerance = max(1, min(1_000_000, expected * 0.0001))
        proof.update(
            registered_shares=count,
            par_value_vnd=par,
            statement_period=end,
            statement_published=rows[0].get("publicDate"),
            charter_capital=capital,
            treasury_capital=treasury_value,
            preferred_capital=preferred,
            latest_registration_date=registry["latest_registration_date"],
            source_url=registry["source_url"],
            observed_at=registry["observed_at"],
            registration_history_basis=registry.get("history_reconciliation"),
            capital_rounding_tolerance_vnd=tolerance,
        )
        if capital is None or abs(capital - expected) > tolerance:
            raise ValueError(
                "Statement-period share basis * par does not reconcile to reported charter capital"
            )
        if treasury_value != 0 or preferred != 0:
            raise ValueError("Treasury/preferred capital is non-zero or unknown")
        proof["verified"] = True
    except (ValueError, KeyError, TypeError) as exc:
        proof["reason"] = str(exc)
    return proof


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


def extend(
    book, prices, raw, asof, observed_at, add, field, *, company_profile=None, share_registry=None, price_basis=None
):
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
    same_session_close = bool(prices and prices[-1]["date"] == asof)
    if market and not aligned:
        reason = "Market cap / stated shares does not match the canonical completed close; do not mix price bases"
    if market and preferred != 0:
        note = "Preferred share capital is non-zero or unknown; common-equity valuation withheld"
        reason = reason + "; " + note if not aligned else note
    market_verified = bool(market and aligned)
    registry_proof = reconcile_registered_shares(
        book, share_registry, profile, end, asof, preferred
    )
    historical = registry_proof.get("source_basis") == "dated_registration_notice"
    if historical and registry_proof["verified"] and not (
        price_basis and price_basis.get("status") == "anchored"
        and price_basis.get("date") == asof
        and price_basis.get("observed_close_vnd") == close
    ):
        registry_proof.update(verified=False, reason="No independently anchored close on analysis date")
    if registry_proof.get("stock_dividend_bridge") and not same_session_close:
        registry_proof.update(verified=False, reason="A completed close on the analysis date is required after the stock dividend")
    independent = bool(registry_proof["verified"] and close is not None and close > 0)
    if historical and not independent:
        reason = registry_proof["reason"]
    share_conflict = bool(
        share_registry
        and share_registry.get("status") == "available"
        and shares is not None
        and share_registry.get("registered_shares") != shares
    )
    provider_verified = bool(market_verified and preferred == 0 and not share_conflict)
    verified = bool(provider_verified or independent)
    calculated_cap = close * registry_proof["registered_shares"] if independent else cap
    if share_conflict:
        reason = (
            "VSDC registration count conflicts with the provider share count; valuation withheld"
        )
    if share_registry:
        proof["registered_share_reconciliation"] = registry_proof
    proof.update(
        market_input_source="company_details" if use_profile else "statistics_financial",
        observed_at=observed.isoformat(),
        statistics_period=list(max(key for key, _ in candidates)) if candidates else None,
        statement_ttm_end=list(end) if end else None,
        quote_comparison={
            "canonical_date": prices[-1]["date"] if prices else None,
            "canonical_close": close,
            "provider_observed_at": observed.isoformat(),
            "provider_quote_time": None,
            "provider_price": number(market.get("currentPrice"))
            if use_profile
            else cap / shares
            if shares and cap
            else None,
            "provider_implied_price": cap / shares if shares and cap else None,
            "matching_prior_canonical_dates": [
                r["date"]
                for r in prices[-6:-1]
                if number(r.get("close")) is not None
                and shares
                and cap
                and abs(number(r["close"]) - cap / shares)
                <= max(0.000001, (cap / shares) * 0.00001)
            ],
            "scope": "Observation time is not quote time. Matching a prior close does not prove lag or adjustment cause; no automatic market-cap rebasing or valuation unlock.",
        },
        marketcap_price_aligned=aligned,
        canonical_close_matches_analysis_date=same_session_close,
        preferred_capital=preferred,
        preferred_field=preferred_fields[0] if len(preferred_fields) == 1 else None,
        status="derived_from_dated_registration_notice"
        if independent and historical
        else "derived_from_reconciled_share_snapshot"
        if independent and not provider_verified
        else "verified_current_inputs"
        if provider_verified
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
    derived_source = (
        (
            "Canonical Vietcap OHLCV close "
            + str(prices[-1]["date"])
            + "; "
            + (registry_proof.get("notice_url") or registry_proof["source_url"])
            + " observed "
            + registry_proof["observed_at"]
            + "; charter/zero treasury/zero preferred as reported "
            + registry_proof["statement_period"]
            + ("; plus dated VSDC stock-dividend issuance" if registry_proof.get("stock_dividend_bridge") else "")
        )
        if independent
        else market_source
    )

    def emit(name, value, unit, formula, *, allow=None, source=None):
        allowed = verified if allow is None else allow
        add(
            name,
            value,
            unit,
            formula if allowed else formula + "; WITHHELD: " + reason,
            source or derived_source,
            asof,
        )

    emit(
        "shares_marketcap_observed",
        shares if market_verified else None,
        "shares",
        "Provider share count used in observed market capitalization; not weighted-average EPS shares",
        allow=market_verified,
        source=market_source,
    )
    if share_registry is not None:
        add(
            "registered_shares",
            share_registry.get("registered_shares")
            if share_registry.get("status") == "available"
            else None,
            "shares",
            "Dated VSDC notice matched to registry; registered count alone does not establish shares outstanding or free float"
            if share_registry.get("source_basis") == "dated_registration_notice"
            else "Current VSDC registered securities; NOT automatically outstanding shares or free float",
            share_registry.get("source_url", "VSDC"),
            asof,
        )
    emit(
        "market_cap_at_canonical_close",
        calculated_cap if (market_verified and not share_conflict) or independent else None,
        "VND",
        (
            "Canonical completed close * registration/charter-reconciled ordinary-share basis; "
            + ("including the dated post-statement stock dividend; " if registry_proof.get("stock_dividend_bridge") else "no post-statement registration change; ")
            + "zero reported treasury/preferred; subsequent undisclosed buybacks not ruled out"
        )
        if independent
        else "Observed market cap, only when cap/shares matches canonical completed close and current issuer inputs or a statistics period matching published statements",
        allow=(market_verified and not share_conflict) or independent,
    )
    emit(
        "pe_ttm",
        calculated_cap / profit if verified and profit is not None and profit > 0 else None,
        "ratio",
        "Market cap / positive reconciled parent-attributable profit TTM; not a sum of quarterly EPS",
    )
    emit(
        "pb",
        calculated_cap / end_equity if verified and end_equity is not None else None,
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
            number(chosen.get(key)) if provider_verified and comparison_aligned else None,
            unit,
            "Provider-reported comparison only; methodology may differ from Python calculation; no historical availability guarantee"
            + (
                ""
                if provider_verified and comparison_aligned
                else "; WITHHELD: provider statistics period or price basis not aligned"
            ),
            "Vietcap statistics-financial observed " + observed.isoformat(),
            asof,
        )
    return proof


PROVIDER_OBSERVATIONS = (
    "shares_marketcap_observed",
    "provider_pe_ttm_observed",
    "provider_pb_observed",
    "provider_roe_ttm_observed",
)


def replay_provider_observations(ticker, asof, close, ttm_end, folder, reports=None):
    """Recover comparison-only fields from a verified same-day saved snapshot."""
    from desktop.vietnam import saved_quantitative_snapshots

    candidates = []
    for snapshot in saved_quantitative_snapshots(ticker, asof, folder, reports):
        try:
            stamp = datetime.fromisoformat(snapshot["valuation_observed_at"])
            if stamp.tzinfo is None:
                continue
            stamp = stamp.astimezone(TZ)
            saved = snapshot["valuation_reconciliation"]
            comparison = saved["quote_comparison"]
            source_name = "company_details" if saved["market_input_source"] == "company_details" else "valuation"
            source = [
                row for row in snapshot["sources"] if row["name"] == source_name
                and len(row.get("sha256", "")) == 64
                and datetime.fromisoformat(row["retrieved_at"]).astimezone(TZ).date().isoformat() == asof
            ]
            if (
                stamp.date().isoformat() != asof or saved["status"] != "verified_current_inputs"
                or not saved["marketcap_price_aligned"] or len(source) != 1
                or comparison["canonical_date"] != asof or comparison["canonical_close"] != close
                or comparison["provider_observed_at"] != snapshot["valuation_observed_at"]
            ):
                continue
            metrics = snapshot["metrics"]
            keys = (
                PROVIDER_OBSERVATIONS if saved["statement_ttm_end"] == ttm_end
                else PROVIDER_OBSERVATIONS[:1]
            )
            records = {
                key: metrics[key] for key in keys
                if key in metrics and metrics[key]["asof"] == asof
                and metrics[key]["status"] == "available"
                and number(metrics[key]["value"]) is not None
            }
            if "shares_marketcap_observed" not in records:
                continue
            shares = records["shares_marketcap_observed"]["value"]
            if shares <= 0 or not float(shares).is_integer():
                continue
            candidates.append((stamp, snapshot["fingerprint"], records, source[0]))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    if not candidates:
        return None
    stamp, fingerprint, records, source = max(candidates, key=lambda item: (item[0], item[1]))
    return {
        "observed_at": stamp.isoformat(),
        "original_snapshot_fingerprint": fingerprint,
        "source_url": source["url"],
        "source_sha256": source["sha256"],
        "fields": {
            key: {**row, "formula": row["formula"] + "; replayed same-day saved provider observation"}
            for key, row in records.items()
        },
    }
