"""Quarter/FY reconciliation must precede flow arithmetic."""

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
