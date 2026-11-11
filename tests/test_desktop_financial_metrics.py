"""Quarter/FY reconciliation must precede flow arithmetic."""

import pytest


from desktop.financial_metrics import reconcile, ttm


def section(values, annual=100):
    return {
        "quarters": [
            {"yearReport": 2025, "lengthReport": q, "value": v, "publicDate": "2026-03-01"}
            for q, v in enumerate(values, 1)
        ],
        "years": [{"yearReport": 2025, "value": annual, "publicDate": "2026-03-01"}],
    }


def test_discrete_quarters_reconcile_to_annual():
    s = section([10, 20, 30, 40])
    v, proof, periods = ttm(s, "value")
    assert proof["basis"] == "discrete" and v == 100 and len(periods) == 4


def test_ytd_is_differenced_not_summed():
    s = section([10, 30, 60, 100])
    v, proof, _ = ttm(s, "value")
    assert proof["basis"] == "ytd" and v == 100


def test_ambiguous_zero_quarters_are_not_summed():
    s = section([0, 0, 0, 100])
    v, proof, _ = ttm(s, "value")
    assert proof["basis"] == "unverified" and v is None


def test_unreconciled_data_is_missing():
    s = section([10, 20, 30, 90])
    v, proof, _ = ttm(s, "value")
    assert proof["basis"] == "unverified" and v is None


def test_conflicting_year_basis_is_rejected():
    s = section([10, 20, 30, 40])
    old = section([10, 30, 60, 100])
    for r in old["quarters"] + old["years"]:
        r["yearReport"] = 2024
    s["quarters"] += old["quarters"]
    s["years"] += old["years"]
    assert reconcile(s, "value")["basis"] == "unverified"


def test_missing_quarter_blocks_ttm_even_with_past_basis():
    s = section([10, 20, 30, 40])
    s["quarters"].extend(
        [
            {"yearReport": 2026, "lengthReport": 1, "value": 30},
            {"yearReport": 2026, "lengthReport": 3, "value": 50},
        ]
    )
    v, proof, _ = ttm(s, "value")
    assert v is None and proof["basis"] == "discrete"


def test_rolling_ytd_ttm_uses_previous_year_q4_and_current_quarters():
    s = section([10, 30, 60, 100])
    s["quarters"].extend(
        [
            {"yearReport": 2026, "lengthReport": 1, "value": 20},
            {"yearReport": 2026, "lengthReport": 2, "value": 50},
            {"yearReport": 2026, "lengthReport": 3, "value": 90},
        ]
    )
    v, _, _ = ttm(s, "value")
    assert v == 130


def test_boolean_and_nonfinite_financials_are_missing():
    for bad in [True, float("nan"), float("inf"), None]:
        s = section([10, 20, 30, bad])
        assert ttm(s, "value")[0] is None


def test_negative_cfo_can_be_reconciled_without_assuming_ytd():
    s = section([-10, 20, 30, 60])
    assert ttm(s, "value")[0] == 100


def test_no_yearly_record_is_not_a_basis_guess():
    s = section([10, 20, 30, 40])
    s["years"] = []
    assert ttm(s, "value")[0] is None


def test_four_contiguous_quarters_required():
    s = section([10, 20, 30, 40])
    s["quarters"].append({"yearReport": 2027, "lengthReport": 1, "value": 10})
    assert ttm(s, "value")[0] is None


def test_stale_reconciliation_does_not_validate_recent_flows():
    s = section([10, 20, 30, 40])
    s["quarters"].extend(
        [{"yearReport": 2030, "lengthReport": q, "value": 10} for q in range(1, 5)]
    )
    assert ttm(s, "value")[0] is None


def test_account_sizing_uses_fees_board_lots_and_reserved_exposure():
    from desktop.account_metrics import evidence

    q = {
        "ticker": "FPT.VN",
        "prices": [{"date": "2026-10-29"}],
        "metrics": {
            "close": {"value": 52700},
            "historical_var95_1d": {"value": 0.03},
            "historical_cvar95_1d": {"value": 0.04},
        },
    }
    book = {
        "mode": "local_backtrader_daily_replay",
        "clock": "2026-10-29",
        "fingerprint": "test-book",
        "nav": 100_000_000,
        "available_cash": 100_000_000,
        "positions": {},
        "reserved_buy_value": {},
        "max_position_fraction": 0.2,
        "commission": 0.0015,
    }
    r = evidence(q, book)["metrics"]
    assert r["max_buy_quantity_at_reference"]["value"] == 300
    assert r["current_ticker_historical_var95_vnd"]["value"] == 0
    book["reserved_buy_value"]["FPT.VN"] = 15_000_000
    assert evidence(q, book)["metrics"]["max_buy_quantity_at_reference"]["value"] == 0
    book["positions"]["FPT.VN"] = {"quantity": 100, "available_quantity": 0}
    r = evidence(q, book)["metrics"]
    assert r["current_ticker_historical_var95_vnd"]["value"] == 158100
    assert r["settled_sell_quantity"]["value"] == 0


def test_absent_account_has_no_fabricated_holdings_or_cash():
    from desktop.account_metrics import evidence

    assert evidence({}, None)["status"] == "unavailable"


def test_positive_capex_is_not_treated_as_cash_outflow():
    from desktop.financial_metrics import extend
    from desktop.vietnam import field

    cash = section([10, 20, 30, 40])
    cash["fields"] = {
        "value": "Net cash inflows/(outflows) from operating activities",
        "capex": "Purchases of fixed assets and other long term assets",
    }
    for r in cash["quarters"]:
        r["capex"] = 5
    cash["years"][0]["capex"] = 20
    book = {
        "cash": cash,
        "income": {"fields": {}, "quarters": [], "years": []},
        "balance": {"fields": {}, "quarters": [], "years": []},
    }
    result = {}
    extend(book, lambda name, value, *args: result.update({name: value}), field)
    assert result["operating_cashflow_ttm"] == 100
    assert result["capex_signed_ttm"] == 20
    assert result["fcf_ttm"] is None


def test_negative_capex_same_periods_yields_defined_fcf():
    from desktop.financial_metrics import extend
    from desktop.vietnam import field

    cash = section([10, 20, 30, 40])
    cash["fields"] = {
        "value": "Net cash inflows/(outflows) from operating activities",
        "capex": "Purchases of fixed assets and other long term assets",
    }
    for r in cash["quarters"]:
        r["capex"] = -5
    cash["years"][0]["capex"] = -20
    book = {
        "cash": cash,
        "income": {"fields": {}, "quarters": [], "years": []},
        "balance": {"fields": {}, "quarters": [], "years": []},
    }
    result = {}
    extend(book, lambda name, value, *args: result.update({name: value}), field)
    assert result["fcf_ttm"] == 80


@pytest.mark.parametrize("interest,expected", [(-5, 6), (5, None)])
def test_interest_and_earnings_proxy_require_reported_expense_sign(interest, expected):
    from desktop.financial_metrics import extend
    from desktop.vietnam import field

    income = section([25, 25, 25, 25])
    income["fields"] = {"value": "Net accounting profit/(loss) before tax", "interest": "Interest expenses"}
    cash = section([2, 2, 2, 2], annual=8)
    cash["fields"] = {"value": "Depreciation and amortization", "paid": "Interest paid"}
    for row in income["quarters"]:
        row["interest"] = interest
    income["years"][0]["interest"] = 4 * interest
    for row in cash["quarters"]:
        row["paid"] = -6
    cash["years"][0]["paid"] = -24
    balance = {
        "fields": {"cash": "Cash and cash equivalents", "short": "Short-term borrowings",
                   "long": "Long-term borrowings"},
        "quarters": [{"yearReport": 2025, "lengthReport": 4, "cash": 10, "short": 20, "long": 30}],
    }
    result = {}
    book = {"income": income, "cash": cash, "balance": balance}
    extend(book, lambda name, value, *args: result.update({name: value}), field)
    assert result["gross_borrowings"] == 50
    assert result["cash_to_short_borrowings"] == 0.5
    assert result["interest_expense_signed_ttm"] == interest * 4
    assert result["interest_paid_signed_ttm"] == -24
    assert result["pretax_plus_interest_coverage_ttm"] == expected
    assert result["ebitda_proxy_ttm"] == (128 if expected is not None else None)
    cash["quarters"].append({**cash["quarters"][0], "yearReport": cash["quarters"][0]["yearReport"] + 1})
    extend(book, lambda name, value, *args: result.update({name: value}), field)
    assert result["ebitda_proxy_ttm"] is None


def test_vhm_bond_note_requires_publication_and_matching_balance_sheet():
    from desktop.debt_notes import load
    from desktop.vietnam import capsule
    import json

    q = {
        "metrics": {
            key: {"value": value, "asof": "2026-Q3 public:2026-10-30"}
            for key, value in (
                ("short_term_borrowings", 65_251_206_000_000),
                ("long_term_borrowings", 175_371_060_000_000),
                ("gross_borrowings", 240_622_266_000_000),
            )
        }
    }
    assert load("VHM", "2026-11-02", q["metrics"]) == {}
    assert load("VIC", "2026-11-10", q["metrics"]) == {}
    notes = load("VHM", "2026-11-10", q["metrics"])
    assert notes["reported_bond_rows"] == 37
    assert notes["bond_carrying_vnd"] == 115_402_180_000_000
    assert notes["bond_current_carrying_vnd"] == 6_078_936_000_000
    loans = notes["bank_and_partner"]
    assert loans["bank_total_vnd"] == 121_778_801_000_000
    assert len(loans["bank_rows"]) == 29
    mb = next(row for row in loans["bank_rows"] if row["lender"] == "MB"
              and row["category"] == "long_term")
    assert (mb["principal_due_from"], mb["principal_due_to"], mb["carrying_vnd"]) == (
        "2028-02", "2030-11", 1_268_750_000_000,
    )
    assert loans["partner_current_narrative_vnd"] != loans["partner_current_carrying_vnd"]
    assert notes["maturities"][0] == {
        "month": "2026-11", "carrying_vnd": 2_088_920_000_000, "pdf_pages": [58]
    }
    assert json.loads(capsule({**q, "debt_notes": notes}))["debt_notes"] == notes
    q["metrics"]["gross_borrowings"]["asof"] = "2026-Q2 public:2026-07-30"
    assert load("VHM", "2026-11-10", q["metrics"]) == {}


def test_roa_ttm_uses_reconciled_same_window_assets():
    from desktop.financial_metrics import extend

    income = section([-10, -20, -30, -40], annual=-100)
    opening = {
        "yearReport": 2024, "lengthReport": 4, "publicDate": "2025-03-01",
        "Total Assets": 400, "Liabilities": 450, "Owner's Equity": -50,
    }
    closing = {
        "yearReport": 2025, "lengthReport": 4, "publicDate": "2026-03-02",
        "Total Assets": 600, "Liabilities": 500, "Owner's Equity": 100,
    }
    later = {**closing, "yearReport": 2026, "lengthReport": 1, "Total Assets": 900}
    balance = {"quarters": [opening, closing, later]}
    book = {"income": income, "balance": balance, "cash": {"quarters": []}}

    def calculate():
        metrics = {}
        extend(book, lambda name, *values: metrics.update({name: values}),
               lambda _book, _kind, title, *_aliases: "value" if title == "Net profit/(loss) after tax" else title)
        return metrics["roa_ttm"]

    assert calculate()[0] == pytest.approx(-0.2)
    assert "2025-03-01" in calculate()[-1] and "2026-03-02" in calculate()[-1]
    opening["Owner's Equity"] = -100
    assert calculate()[0] is None
    opening["Owner's Equity"] = -50
    balance["quarters"].append(closing.copy())
    assert calculate()[0] is None


def test_operating_margin_requires_both_flows_to_reconcile():
    from desktop.financial_metrics import extend

    income = section([10, 20, 30, 40])
    for row, operating_profit in zip(income["quarters"], [2, 5, 7, 6]):
        row["Operating profit/(loss)"] = operating_profit
    annual = income["years"][0]
    annual["Operating profit/(loss)"] = 20
    book = {"income": income, "balance": {"quarters": []}, "cash": {"quarters": []}}
    metrics = {}

    def calculate():
        extend(
            book,
            lambda name, *values: metrics.update({name: values}),
            lambda _book, _kind, title, *_aliases: "value" if title == "Net sales" else title,
        )

    calculate()
    assert metrics["operating_profit_ttm"][0] == 20
    assert metrics["operating_margin_ttm"][0] == pytest.approx(0.20)
    annual["Operating profit/(loss)"] = 25
    calculate()
    assert metrics["operating_margin_ttm"][0] is None
