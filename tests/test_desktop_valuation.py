"""A current observation must never become a historical, guessed valuation."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from desktop.valuation import extend
from desktop.vietnam import field

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def independent_fixture():
    book, raw = fixture()
    section = book["balance"]
    section["fields"]["treasury"] = "Treasury shares"
    section["field_metadata"].update(capital={"parent": "equity"}, treasury={"parent": "capital"})
    for row in section["quarters"]:
        row.update(capital=1000, treasury=0)
    profile = {
        "status": "available",
        "ticker": "AAA.VN",
        "observed_at": "2026-03-02T10:00:00+07:00",
        "market": {
            "numberOfSharesMktCap": 100,
            "issueShare": 100,
            "currentPrice": 11,
            "marketCap": 1100,
        },
    }
    registry = {
        "status": "available",
        "ticker": "AAA.VN",
        "observed_at": profile["observed_at"],
        "registered_shares": 100,
        "par_value_vnd": 10,
        "history_reconciled": True,
        "latest_registration_date": "2025-12-01",
        "source_url": "https://vsdc.vn/vi/s-detail/12",
    }
    raw["data"][0]["marketCap"] = 1100
    return book, raw, profile, registry


def independent_calculate(book, raw, profile, registry, *, price_basis=None, price_date="2026-03-02"):
    result = {}

    def add(name, value, unit, formula, source, stamp):
        result[name] = {"value": value, "formula": formula, "source": source}

    proof = extend(
        book,
        [{"date": price_date, "close": 10}],
        raw,
        "2026-03-02",
        datetime(2026, 3, 2, 12, tzinfo=TZ),
        add,
        field,
        company_profile=profile,
        share_registry=registry,
        price_basis=price_basis,
    )
    return result, proof


def test_independent_share_basis_computes_with_canonical_price_and_retains_mismatch():
    r, p = independent_calculate(*independent_fixture())
    assert p["status"] == "derived_from_reconciled_share_snapshot"
    assert not p["marketcap_price_aligned"]
    assert p["quote_comparison"]["provider_price"] == 11
    assert r["market_cap_at_canonical_close"]["value"] == 1000
    assert r["pe_ttm"]["value"] == 10 and r["pb"]["value"] == pytest.approx(1000 / 600)
    assert r["provider_pe_ttm_observed"]["value"] is None
    assert r["provider_pb_observed"]["value"] is None
    assert r["registered_shares"]["value"] == 100
    assert "registration/charter-reconciled" in r["market_cap_at_canonical_close"]["formula"]
    assert "vsdc.vn" in r["pe_ttm"]["source"]


@pytest.mark.parametrize(
    "code,ratio_type,expected", [("AAA1", "RATIO_TTM", 0.21), ("BBB1", "RATIO_TTM", None),
                                 ("AAA1", "RATIO_YEAR", None)]
)
def test_provider_share_count_and_ttm_roe_do_not_depend_on_quote(code, ratio_type, expected):
    book, raw, profile, registry = independent_fixture()
    profile["provider_issuer_code"] = "AAA1"
    raw["data"][0].update(organCode=code, ratioType=ratio_type)
    result, proof = independent_calculate(book, raw, profile, registry)
    assert not proof["marketcap_price_aligned"]
    assert proof["provider_share_count_independently_reconciled"]
    assert result["shares_marketcap_observed"]["value"] == 100
    assert result["provider_roe_ttm_observed"]["value"] == expected
    assert result["provider_pe_ttm_observed"]["value"] is None
    assert result["provider_pb_observed"]["value"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "count",
        "treasury",
        "treasury_missing",
        "preferred",
        "capital",
        "later_registration",
        "old_snapshot",
        "future_publication",
        "wrong_identity",
        "cached_error",
        "treasury_wrong_parent",
    ],
)
def test_registry_alone_never_unlocks_unreconciled_common_equity(fault):
    book, raw, profile, registry = independent_fixture()
    if fault == "count":
        registry["registered_shares"] = 101
    elif fault == "treasury":
        book["balance"]["quarters"][-1]["treasury"] = -10
    elif fault == "treasury_missing":
        book["balance"]["quarters"][-1]["treasury"] = None
    elif fault == "preferred":
        book["balance"]["quarters"][-1]["pref_equity"] = 1
    elif fault == "capital":
        book["balance"]["quarters"][-1]["capital"] = 1002
    elif fault == "later_registration":
        registry["latest_registration_date"] = "2026-01-01"
    elif fault == "old_snapshot":
        registry["observed_at"] = "2026-03-01T12:00:00+07:00"
    elif fault == "future_publication":
        book["balance"]["quarters"][-1]["publicDate"] = "2026-03-03"
    elif fault == "wrong_identity":
        registry["ticker"] = "BBB.VN"
    elif fault == "cached_error":
        registry["status"] = "cached_source_error"
    elif fault == "treasury_wrong_parent":
        book["balance"]["field_metadata"]["treasury"]["parent"] = "debt"
    r, p = independent_calculate(book, raw, profile, registry)
    assert r["pe_ttm"]["value"] is None and r["pb"]["value"] is None
    assert not p["registered_share_reconciliation"]["verified"]


def fixture():
    income = {
        "fields": {"profit": "Attributable to parent company"},
        "quarters": [
            {"yearReport": 2025, "lengthReport": q, "profit": v}
            for q, v in enumerate([10, 20, 30, 40], 1)
        ],
        "years": [{"yearReport": 2025, "profit": 100}],
    }
    balance = {
        "fields": {
            "assets": "Total Assets",
            "debt": "Liabilities",
            "equity": "Owner's Equity",
            "nci": "Minority interests",
            "capital": "Paid-in capital",
            "pref_debt": "Preferred shares",
            "pref_equity": "Preferred shares",
        },
        "field_metadata": {
            "pref_debt": {"titleEn": "Preferred shares", "parent": "DEBT"},
            "pref_equity": {"titleEn": "Preferred shares", "parent": "CAPITAL"},
        },
        "quarters": [
            {
                "yearReport": year,
                "lengthReport": 4,
                "assets": assets,
                "debt": 100,
                "equity": assets - 100,
                "nci": 100,
                "pref_debt": None,
                "pref_equity": 0,
                "publicDate": f"{year + 1}-03-01",
            }
            for year, assets in [(2024, 600), (2025, 800)]
        ],
        "years": [],
    }
    raw = {
        "data": [
            {
                "yearReport": 2025,
                "quarter": 4,
                "numberOfSharesMktCap": 100,
                "marketCap": 1000,
                "pe": 10,
                "pb": 1.7,
                "roe": 0.21,
            }
        ]
    }
    return {"income": income, "balance": balance}, raw


def calculate(book=None, raw=None, asof="2026-03-02", observed=None):
    default, default_raw = fixture()
    result = {}

    def add(name, value, unit, formula, source, stamp):
        result[name] = {
            "value": value,
            "unit": unit,
            "formula": formula,
            "source": source,
            "asof": stamp,
        }

    proof = extend(
        book or default,
        [{"date": "2026-02-27", "close": 10}],
        raw if raw is not None else default_raw,
        asof,
        observed or datetime(2026, 3, 2, 10, tzinfo=TZ),
        add,
        field,
    )
    return result, proof


def test_current_marketcap_pe_pb_and_parent_scoped_roe():
    r, p = calculate()
    assert p["status"] == "verified_current_inputs"
    assert r["pe_ttm"]["value"] == 10
    assert r["pb"]["value"] == pytest.approx(1000 / 600)
    assert r["roe_ttm"]["value"] == pytest.approx(100 / 500)
    assert r["equity_parent_begin_ttm"]["value"] == 400
    assert r["equity_parent_end_ttm"]["value"] == 600
    assert r["provider_roe_ttm_observed"]["value"] == 0.21
    assert r["roe_ttm"]["value"] != r["provider_roe_ttm_observed"]["value"]


def test_historical_valuation_withheld_even_if_period_is_old():
    r, p = calculate(asof="2026-03-01")
    assert p["status"] == "withheld"
    for key in ["pe_ttm", "pb", "market_cap_at_canonical_close", "provider_pe_ttm_observed"]:
        assert r[key]["value"] is None
    # ROE comes from published-dated statements, not a current price observation.
    assert r["roe_ttm"]["value"] == 0.2


@pytest.mark.parametrize(
    "key,value",
    [
        ("marketCap", 1100),
        ("numberOfSharesMktCap", 101),
        ("numberOfSharesMktCap", True),
        ("marketCap", float("nan")),
    ],
)
def test_mismatched_price_basis_or_bad_numbers_do_not_make_pe(key, value):
    book, raw = fixture()
    raw["data"][0][key] = value
    r, p = calculate(book, raw)
    assert r["pe_ttm"]["value"] is None and r["pb"]["value"] is None


def test_missing_nci_is_not_inferred_to_be_zero():
    book, raw = fixture()
    del book["balance"]["quarters"][0]["nci"]
    r, p = calculate(book, raw)
    assert r["roe_ttm"]["value"] is None
    assert r["pb"]["value"] is not None  # End equity remains available.


def test_assets_liabilities_equity_must_reconcile():
    book, raw = fixture()
    book["balance"]["quarters"][-1]["equity"] = 650
    r, p = calculate(book, raw)
    assert r["roe_ttm"]["value"] is None and r["pb"]["value"] is None


def test_missing_beginning_or_duplicate_balance_period_blocks_roe():
    book, raw = fixture()
    book["balance"]["quarters"].append(deepcopy(book["balance"]["quarters"][0]))
    r, p = calculate(book, raw)
    assert r["roe_ttm"]["value"] is None


@pytest.mark.parametrize(
    "change",
    ["future_period", "duplicate", "missing_pref", "preferred_issued", "unknown_hierarchy"],
)
def test_inconsistent_statistics_or_equity_class_withheld(change):
    book, raw = fixture()
    if change == "future_period":
        raw["data"][0]["yearReport"] = 2026
    elif change == "duplicate":
        raw["data"].append(deepcopy(raw["data"][0]))
    elif change == "missing_pref":
        del book["balance"]["quarters"][-1]["pref_equity"]
    elif change == "preferred_issued":
        book["balance"]["quarters"][-1]["pref_equity"] = 10
    else:
        book["balance"]["field_metadata"] = {}
    r, p = calculate(book, raw)
    assert r["pe_ttm"]["value"] is None and r["pb"]["value"] is None


def test_unreconciled_profit_never_used_in_pe_or_roe():
    book, raw = fixture()
    book["income"]["quarters"][-1]["profit"] = 42
    r, p = calculate(book, raw)
    assert r["pe_ttm"]["value"] is None and r["roe_ttm"]["value"] is None


def test_negative_profit_not_presented_as_positive_pe():
    book, raw = fixture()
    for row in book["income"]["quarters"] + book["income"]["years"]:
        row["profit"] *= -1
    r, p = calculate(book, raw)
    assert r["pe_ttm"]["value"] is None and r["roe_ttm"]["value"] < 0


def test_no_statistics_still_allows_statement_roe():
    r, p = calculate(raw={})
    assert r["pe_ttm"]["value"] is None and r["roe_ttm"]["value"] == 0.2


def test_bank_metadata_aliases_preserve_parent_equity_scope():
    book, raw = fixture()
    book["balance"]["fields"]["debt"] = "TOTAL LIABILITIES"
    book["balance"]["fields"]["nci"] = "Minority interest"
    r, p = calculate(book, raw)
    assert r["roe_ttm"]["value"] == 0.2


def test_current_issuer_details_can_supply_cap_when_statistics_are_stale():
    book, raw = fixture()
    raw["data"][0]["yearReport"] = 2024
    from desktop.company_profile import normalize

    observed = datetime(2026, 3, 2, 10, tzinfo=TZ)
    profile = normalize(
        {
            "data": {
                "ticker": "HPG",
                "currentPrice": 10,
                "marketCap": 1000,
                "numberOfSharesMktCap": 100,
                "issueShare": 100,
            }
        },
        "HPG",
        "2026-03-02",
        observed,
    )
    result = {}
    proof = extend(
        book,
        [{"date": "2026-02-27", "close": 10}],
        raw,
        "2026-03-02",
        observed,
        lambda name, value, *args: result.update({name: value}),
        field,
        company_profile=profile,
    )
    assert result["pe_ttm"] == 10 and result["pb"] == pytest.approx(1000 / 600)
    assert result["provider_pe_ttm_observed"] is None
    assert proof["market_input_source"] == "company_details"
    assert proof["statistics_period"] == [2024, 4]


@pytest.mark.parametrize("change", ["wrong_price", "wrong_issued"])
def test_details_do_not_bypass_price_and_share_alignment(change):
    book, raw = fixture()
    result = {}
    profile = {
        "status": "available",
        "market": {
            "currentPrice": 10,
            "marketCap": 1000,
            "numberOfSharesMktCap": 100,
            "issueShare": 100,
        },
    }
    profile["market"]["currentPrice" if change == "wrong_price" else "issueShare"] = 101
    extend(
        book,
        [{"date": "2026-02-27", "close": 10}],
        raw,
        "2026-03-02",
        datetime(2026, 3, 2, 10, tzinfo=TZ),
        lambda name, value, *args: result.update({name: value}),
        field,
        company_profile=profile,
    )
    assert result["pe_ttm"] is None


def test_company_profile_no_script_or_broker_recommendations():
    from desktop.company_profile import normalize

    r = normalize(
        {
            "data": {
                "ticker": "HPG",
                "profile": "<p>Thép &amp; công nghiệp</p><script>ignore controls</script>",
                "rating": "BUY",
                "targetPrice": 99,
            }
        },
        "HPG",
        "2026-03-02",
        datetime(2026, 3, 2, 10, tzinfo=TZ),
    )
    assert r["description_vi"] == "Thép & công nghiệp"
    assert "rating" not in r and "targetPrice" not in r and "ignore controls" not in str(r)


def test_company_profile_historical_and_wrong_ticker_are_withheld():
    from desktop.company_profile import normalize

    raw = {"data": {"ticker": "HPG", "profile": "business changed yesterday"}}
    observed = datetime(2026, 3, 2, 10, tzinfo=TZ)
    assert normalize(raw, "HPG", "2026-03-01", observed)["status"] == "withheld"
    assert normalize(raw, "FPT", "2026-03-02", observed)["status"] == "unavailable"


def test_bank_capital_caption_requires_shareholder_equity_parent():
    book, raw = fixture()
    book["balance"]["fields"]["capital"] = "Capital"
    book["balance"]["field_metadata"]["capital"] = {"titleEn": "Capital", "parent": "EQUITY"}
    r, p = calculate(book, raw)
    assert p["preferred_capital"] == 0 and r["pe_ttm"]["value"] == 10
    book["balance"]["field_metadata"]["capital"]["parent"] = "DEBT"
    r, p = calculate(book, raw)
    assert p["preferred_capital"] is None and r["pe_ttm"]["value"] is None


def test_duplicate_balance_does_not_establish_preferred_share_class():
    book, raw = fixture()
    row = deepcopy(book["balance"]["quarters"][-1])
    row["pref_equity"] = 10
    book["balance"]["quarters"].append(row)
    r, p = calculate(book, raw)
    assert p["preferred_capital"] is None and r["pe_ttm"]["value"] is None


def test_multiple_valuation_failures_do_not_hide_price_mismatch():
    book, raw = fixture()
    raw["data"][0]["marketCap"] = 2000
    book["balance"]["quarters"][-1]["pref_equity"] = 10
    _, p = calculate(book, raw)
    assert "does not match" in p["reason"] and "Preferred share" in p["reason"]


def test_price_comparison_does_not_invent_provider_quote_time_or_unlock_mismatch():
    book, raw = fixture()
    raw["data"][0]["marketCap"] = 2000
    result, proof = calculate(book, raw)
    comparison = proof["quote_comparison"]
    assert comparison["provider_quote_time"] is None
    assert comparison["provider_implied_price"] == 20
    assert comparison["canonical_close"] == 10
    assert result["pe_ttm"]["value"] is None and not proof["marketcap_price_aligned"]


def test_dated_registration_uses_anchored_close_and_never_current_broker_ratios():
    book, _, _, registry = independent_fixture()
    registry.update(
        source_basis="dated_registration_notice", analysis_date="2026-03-02",
        notice_url="https://vsdc.vn/vi/ad/42", notice_published="2025-12-01T09:00:00+07:00",
        observed_at="2026-03-04T09:00:00+07:00",
    )
    result, proof = independent_calculate(
        book, {}, {}, registry,
        price_basis={"status": "anchored", "date": "2026-03-02", "observed_close_vnd": 10},
    )
    assert proof["status"] == "derived_from_dated_registration_notice"
    assert result["pe_ttm"]["value"] == 10 and result["pb"]["value"] == pytest.approx(1000 / 600)
    assert result["provider_pe_ttm_observed"]["value"] is None
    without_anchor, _ = independent_calculate(book, {}, {}, registry)
    assert without_anchor["pe_ttm"]["value"] is None
    assert without_anchor["registered_shares"]["value"] == 100


@pytest.mark.parametrize(("reason", "published", "expected"), [
    ("Trả cổ tức bằng cổ phiếu", "2026-01-10", 1200),
    ("Trả cổ tức bằng cổ phiếu", "2026-03-03", None),
    ("Phát hành thu tiền", "2026-01-10", None),
])
def test_anchored_valuation_bridges_only_confirmed_stock_dividends(reason, published, expected):
    book, _, _, registry = independent_fixture()
    event = {"date": "2026-01-04", "change": 20, "reason": reason}
    registry.update(
        source_basis="dated_registration_notice", analysis_date="2026-03-02",
        notice_url="https://vsdc.vn/vi/ad/42", notice_published="2026-01-05T09:00:00+07:00",
        observed_at="2026-03-04T09:00:00+07:00", latest_registration_date=event["date"],
        registration_history=[event], registered_shares=120, notice_registration_change=event,
        issuer_capital_change={
            "effective_date": "2026-01-07", "published": published, "before_shares": 100,
            "after_shares": 120, "par_value_vnd": 10,
        },
    )
    result, proof = independent_calculate(
        book, {}, {}, registry,
        price_basis={"status": "anchored", "date": "2026-03-02", "observed_close_vnd": 10},
    )
    assert result["market_cap_at_canonical_close"]["value"] == expected
    if expected:
        assert result["pe_ttm"]["value"] == 12 and result["pb"]["value"] == 2
        assert proof["registered_share_reconciliation"]["stock_dividend_bridge"]["statement_share_basis"] == 100
    else:
        assert not proof["registered_share_reconciliation"]["verified"]


def test_same_day_issuer_change_allows_canonical_valuation_with_lagging_broker():
    book, raw, profile, registry = independent_fixture()
    event = {"date": "2026-01-04", "change": 20, "reason": "Trả cổ tức bằng cổ phiếu"}
    registry.update(
        latest_registration_date=event["date"], registration_history=[event],
        registered_shares=120, notice_registration_change=event,
        notice_published="2026-01-05T09:00:00+07:00",
        issuer_capital_change={
            "effective_date": "2026-01-07", "published": "2026-01-10",
            "before_shares": 100, "after_shares": 120, "par_value_vnd": 10,
        },
    )
    profile["market"].update(numberOfSharesMktCap=120, issueShare=120, marketCap=1320)
    result, proof = independent_calculate(book, raw, profile, registry)
    assert proof["status"] == "derived_from_reconciled_share_snapshot"
    assert result["market_cap_at_canonical_close"]["value"] == 1200
    assert result["provider_pe_ttm_observed"]["value"] is None
    previous, proof = independent_calculate(book, raw, profile, registry, price_date="2026-02-27")
    assert previous["market_cap_at_canonical_close"]["value"] is None
    assert not proof["canonical_close_matches_analysis_date"]


def test_preclose_provider_observation_is_labelled_with_prior_completed_session():
    book, raw, profile, _ = independent_fixture()
    profile["market"].update(currentPrice=10, marketCap=1000)
    result, proof = independent_calculate(book, raw, profile, {}, price_date="2026-02-27")
    assert proof["marketcap_price_aligned"] and not proof["canonical_close_matches_analysis_date"]
    assert result["shares_marketcap_observed"]["value"] == 100
    assert "2026-02-27" in result["shares_marketcap_observed"]["source"]


def test_provider_replay_requires_same_day_same_close_and_intact_saved_snapshot(tmp_path):
    from desktop.valuation import replay_provider_observations
    from desktop.vietnam import digest, write_json

    stamp = "2026-03-02T16:00:00+07:00"
    item = {
        "ticker": "AAA.VN", "analysis_date": "2026-03-02", "valuation_observed_at": stamp,
        "valuation_reconciliation": {
            "status": "verified_current_inputs", "marketcap_price_aligned": True,
            "market_input_source": "company_details", "statement_ttm_end": [2025, 4],
            "quote_comparison": {
                "canonical_date": "2026-03-02", "canonical_close": 10, "provider_observed_at": stamp,
            },
        },
        "sources": [{"name": "company_details", "retrieved_at": stamp, "sha256": "a" * 64,
                     "url": "https://iq.vietcap.com.vn/"}],
        "metrics": {"shares_marketcap_observed": {"asof": "2026-03-02", "value": 100,
                                                 "status": "available", "formula": "Observed"},
                    "provider_pe_ttm_observed": {"asof": "2026-03-02", "value": 10,
                                                 "status": "available", "formula": "Observed"}},
    }
    item["fingerprint"] = digest({k: v for k, v in item.items() if k != "sources"})
    path = tmp_path / "AAA_2026-03-02_v31_news20.json"
    write_json(path, item)

    def replay(close=10, period=(2025, 4)):
        return replay_provider_observations("AAA.VN", "2026-03-02", close, list(period), tmp_path)

    assert replay()["fields"]["shares_marketcap_observed"]["value"] == 100
    assert replay(close=11) is None
    assert replay()["fields"]["provider_pe_ttm_observed"]["value"] == 10
    assert set(replay(period=(2025, 3))["fields"]) == {"shares_marketcap_observed"}
    item["metrics"]["shares_marketcap_observed"]["value"] = 101
    write_json(path, item)
    assert replay() is None


def test_immutable_provider_receipt_survives_rolling_cache_replacement(tmp_path):
    from desktop.valuation import archive_provider_observation, replay_provider_observations
    from desktop.vietnam import digest, write_json

    day = "2026-03-02"

    def snapshot(hour, include_stats):
        stamp = f"{day}T{hour}:00:00+07:00"
        row = {"asof": day, "value": 100, "status": "available", "formula": "Observed"}
        data = {
            "ticker": "AAA.VN",
            "analysis_date": day,
            "prices": [{"date": day, "close": 10}],
            "valuation_observed_at": stamp,
            "valuation_reconciliation": {
                "status": "verified_current_inputs", "marketcap_price_aligned": True,
                "market_input_source": "company_details", "statement_ttm_end": [2025, 4],
                "quote_comparison": {
                    "canonical_date": day, "canonical_close": 10, "provider_observed_at": stamp,
                },
            },
            "metrics": {
                "shares_marketcap_observed": row,
                "provider_pe_ttm_observed": {**row, "value": 10},
            },
            "sources": [{"name": "company_details", "retrieved_at": stamp, "sha256": "a" * 64,
                         "url": "https://iq.vietcap.com.vn/details"}],
        }
        if include_stats:
            data["sources"].append({"name": "valuation", "retrieved_at": stamp, "sha256": "b" * 64,
                                    "url": "https://iq.vietcap.com.vn/statistics-financial"})
        data["fingerprint"] = digest({k: v for k, v in data.items() if k != "sources"})
        return data

    complete = snapshot("16", True)
    partial = snapshot("17", False)
    archive_provider_observation(complete, tmp_path)
    archive_provider_observation(partial, tmp_path)
    rolling = tmp_path / f"AAA_{day}_v32_news20.json"
    write_json(rolling, {"status": "replaced"})
    def replay(period=(2025, 4)):
        return replay_provider_observations("AAA.VN", day, 10, list(period), tmp_path)

    assert replay()["fields"]["provider_pe_ttm_observed"]["value"] == 10
    assert {row["name"] for row in replay()["source_receipts"]} == {"company_details", "valuation"}
    assert replay()["original_snapshot_fingerprint"] == complete["fingerprint"]
    assert set(replay((2025, 3))["fields"]) == {"shares_marketcap_observed"}

    derived = snapshot("18", True)
    derived["valuation_reconciliation"]["provider_archive"] = {"observed_at": complete["valuation_observed_at"]}
    archive_provider_observation(derived, tmp_path)
    receipts = list(tmp_path.glob("valuation-observations/AAA/*/*.json"))
    assert len(receipts) == 2
    full = next(path for path in receipts if '"provider_pe_ttm_observed"' in path.read_text())
    full.write_text(full.read_text().replace('"value": 10,', '"value": 11,'))
    assert set(replay()["fields"]) == {"shares_marketcap_observed"}
    assert replay_provider_observations("AAA.VN", day, 11, [2025, 4], tmp_path) is None
