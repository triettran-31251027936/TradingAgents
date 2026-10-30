import copy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from desktop import bank_metrics as b


def fixture():
    bf = {f"b{i}": title for i, title in enumerate(b.STOCKS.values())}
    inf = {f"i{i}": title for i, title in enumerate(b.FLOWS.values())}
    balance = {
        "fields": bf,
        "quarters": [
            {
                "yearReport": 2026,
                "lengthReport": 2,
                "publicDate": "2026-08-19",
                **dict(zip(bf, [1000, 980, -20, 800, 200, 50, 60], strict=True)),
            }
        ],
        "years": [],
    }

    def flow(year, q):
        return {
            "yearReport": year,
            "lengthReport": q,
            "publicDate": "2026-08-19",
            **dict(zip(inf, [40, 60, -20, 10, 60, -18, 42, -3], strict=True)),
        }

    income = {
        "fields": inf,
        "quarters": [flow(2025, q) for q in range(1, 5)] + [flow(2026, q) for q in (1, 2)],
        "years": [{**flow(2025, 5), **{k: v * 4 for k, v in flow(2025, 5).items() if k in inf}}],
    }
    raw = {
        "data": [
            {
                "yearReport": 2026,
                "quarter": 2,
                "netInterestMargin": 0.03,
                "npl": 0.01,
                "casaRatio": 0.3,
                "costToIncome": -0.3,
            }
        ]
    }
    return {"balance": balance, "income": income}, raw


def calc(book=None, raw=None, asof="2026-10-30"):
    default, dr = fixture()
    values = {}

    def add(n, v, u, f, s, d):
        values[n] = {"value": v, "unit": u, "formula": f, "source": s, "asof": d}

    proof = b.extend(
        book or default,
        add,
        valuation_raw=dr if raw is None else raw,
        asof=asof,
        observed_at=datetime(2026, 10, 30, 17, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )
    return values, proof


def test_bank_accounting_stock_ratios_and_signed_ttm():
    m, p = calc()
    assert p["sector"] == "bank" and p["loan_net_reconciliation"]["verified"]
    assert m["bank_accounting_loan_deposit_ratio"]["value"] == 1.25
    assert "NOT SBV regulatory" in m["bank_accounting_loan_deposit_ratio"]["formula"]
    assert m["bank_loan_allowance_ratio"]["value"] == 0.02
    assert m["bank_net_interest_income_ttm"]["value"] == 160
    assert m["bank_cost_income_ratio_ttm"]["value"] == 0.3
    assert m["bank_noninterest_income_ttm"]["value"] == 80
    assert m["bank_provider_cost_income_signed_observed"]["value"] == -0.3
    assert m["bank_provider_nim_observed"]["value"] == 0.03
    assert "not independently verified" in m["bank_provider_nim_observed"]["formula"]


@pytest.mark.parametrize("bad", [20, None, -30])
def test_bad_allowance_sign_missing_or_net_mismatch_does_not_make_coverage(bad):
    book, raw = fixture()
    book["balance"]["quarters"][0]["b2"] = bad
    m, p = calc(book, raw)
    assert not p["loan_net_reconciliation"]["verified"]
    assert m["bank_loan_allowance_ratio"]["value"] is None


def test_zero_deposit_is_not_division_or_fabricated_ldr():
    book, raw = fixture()
    book["balance"]["quarters"][0]["b3"] = 0
    m, _ = calc(book, raw)
    assert m["bank_accounting_loan_deposit_ratio"]["value"] is None


@pytest.mark.parametrize("mode", ["duplicate", "old", "wrong_balance", "historical"])
def test_current_provider_ratios_require_unique_matching_period(mode):
    book, raw = fixture()
    asof = "2026-10-30"
    if mode == "duplicate":
        raw["data"] *= 2
    if mode == "old":
        raw["data"][0]["quarter"] = 1
    if mode == "wrong_balance":
        book["balance"]["quarters"][0]["lengthReport"] = 1
    if mode == "historical":
        asof = "2026-10-29"
    m, p = calc(book, raw, asof)
    assert p["provider_comparison"]["status"] == "withheld"
    assert m["bank_provider_nim_observed"]["value"] is None


@pytest.mark.parametrize("mode", ["duplicate", "missing_quarter", "conflicting_annual"])
def test_unproven_ttm_is_not_summed(mode):
    book, raw = fixture()
    if mode == "duplicate":
        book["income"]["quarters"].append(copy.deepcopy(book["income"]["quarters"][-1]))
    if mode == "missing_quarter":
        book["income"]["quarters"].pop(3)
    if mode == "conflicting_annual":
        book["income"]["years"][0]["i0"] = 9999
    m, p = calc(book, raw)
    assert m["bank_net_interest_income_ttm"]["value"] is None
    assert p["flows"]["bank_net_interest_income_ttm"]["basis"] == "unverified"


def test_industrial_statement_does_not_acquire_bank_metrics():
    book, raw = fixture()
    book["income"]["fields"] = {"sales": "Net sales"}
    m, p = calc(book, raw)
    assert not m and not p


def test_ytd_flows_are_differenced_before_ttm_ratio():
    book, raw = fixture()
    for row in book["income"]["quarters"]:
        for k in book["income"]["fields"]:
            row[k] *= row["lengthReport"]
    m, p = calc(book, raw)
    assert p["flows"]["bank_net_interest_income_ttm"]["basis"] == "ytd"
    assert m["bank_net_interest_income_ttm"]["value"] == 160
    assert m["bank_cost_income_ratio_ttm"]["value"] == 0.3


def test_duplicate_balance_period_does_not_pick_one_loan_stock():
    book, raw = fixture()
    row = copy.deepcopy(book["balance"]["quarters"][0])
    row["b0"] = 9999
    book["balance"]["quarters"].append(row)
    m, p = calc(book, raw)
    assert p["ambiguous_balance_period"]
    assert m["bank_customer_loans_gross"]["value"] is None
    assert m["bank_accounting_loan_deposit_ratio"]["value"] is None
    assert m["bank_provider_ldr_observed"]["value"] is None
