from copy import deepcopy

from desktop.applicability import BANK_INDUSTRIAL_ONLY, apply_bank, missing
from desktop.financial_metrics import extend
from desktop.vietnam import field


def test_non_bank_metrics_are_preserved():
    metrics = {"gross_margin": {"value": 0.5, "status": "available", "formula": "gross/revenue"}}
    before = deepcopy(metrics)
    apply_bank(metrics, {})
    assert metrics == before


def test_bank_exclusions_are_not_zero_and_do_not_hide_real_missing_inputs():
    metrics = {
        k: {"value": 100, "status": "available", "formula": "original formula"}
        for k in BANK_INDUSTRIAL_ONLY
    }
    metrics["bank_customer_deposits"] = {
        "value": None,
        "status": "missing",
        "formula": "missing source",
    }
    metrics["operating_cashflow_ttm"] = {
        "value": 200,
        "status": "available",
        "formula": "deposit driven CFO",
    }
    apply_bank(metrics, {"sector": "bank"})
    assert all(
        metrics[k]["value"] is None and metrics[k]["status"] == "not_applicable"
        for k in BANK_INDUSTRIAL_ONLY
    )
    assert missing(metrics) == ["bank_customer_deposits"]
    assert metrics["operating_cashflow_ttm"]["value"] == 200


def test_bank_cfo_caption_alias_is_reconciled_without_inventing_fcf():
    def rows(periods):
        return [
            {
                "yearReport": y,
                "lengthReport": q,
                "publicDate": "2026-08-19",
                "cfo": 10 if q != 5 else 40,
                "capex": -1 if q != 5 else -4,
            }
            for y, q in periods
        ]

    cash = {
        "fields": {
            "cfo": "Net cash from operating activities",
            "capex": "Purchases of fixed assets and other long term assets",
        },
        "quarters": rows([(2025, q) for q in range(1, 5)] + [(2026, 1), (2026, 2)]),
        "years": rows([(2025, 5)]),
    }
    empty = {"fields": {}, "quarters": [], "years": []}
    book = {"cash": cash, "income": deepcopy(empty), "balance": deepcopy(empty)}
    metrics = {}

    def add(n, v, u, f, s, d):
        metrics[n] = {
            "value": v,
            "status": "available" if v is not None else "missing",
            "formula": f,
        }

    extend(book, add, field)
    assert metrics["operating_cashflow_ttm"]["value"] == 40 and metrics["fcf_ttm"]["value"] == 36
    apply_bank(metrics, {"sector": "bank"})
    assert metrics["operating_cashflow_ttm"]["value"] == 40 and metrics["fcf_ttm"]["value"] is None
    assert metrics["fcf_ttm"]["status"] == "not_applicable"


def test_model_handoff_distinguishes_inapplicable_from_real_gaps(tmp_path):
    from langchain_core.messages import AIMessage, HumanMessage

    from desktop.runtime_models import Runtime

    metrics = {
        "close": {"value": 50000, "status": "available"},
        "fcf_ttm": {"value": None, "status": "not_applicable"},
        "bank_provider_nim_observed": {"value": None, "status": "missing"},
    }
    q = {
        "ticker": "VCB.VN",
        "analysis_date": "2026-10-30",
        "metrics": metrics,
        "missing": ["bank_provider_nim_observed"],
        "fingerprint": "bank-canonical",
        "warnings": [],
        "basis": [],
    }
    runtime = Runtime({"data_cache_dir": str(tmp_path)}, quant=q)
    messages = runtime.prompt(
        [HumanMessage(content="No inference transport test")], "Portfolio Manager"
    )
    runtime._capture("Portfolio Manager", AIMessage(content="Transport only"), value=messages)
    receipt = runtime.handoffs["Portfolio Manager"]
    assert receipt["missing_metrics"] == ["bank_provider_nim_observed"]
    assert receipt["not_applicable_metrics"] == ["fcf_ttm"] and receipt["metric_count"] == 3
