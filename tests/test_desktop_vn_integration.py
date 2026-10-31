"""Behavioral checks for VN grounding and consent-gated virtual execution."""

import hashlib
import hmac
import json

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from desktop import vietnam
from desktop.paper import Store, replay, validate_order
from desktop.runtime_models import ExecutionIntent, Runtime


def bars():
    return [
        {"date": day, "open": 50000, "high": 51000, "low": 49000, "close": 50000, "volume": 100000}
        for day in ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    ]


def bundle():
    return {
        "ticker": "FPT.VN",
        "analysis_date": "2026-10-26",
        "fingerprint": "fixture-only",
        "prices": bars()[:1],
        "metrics": {
            k: {"value": 1}
            for k in ["close", "adv20", "volatility20_annual", "historical_var95_1d"]
        },
    }


def intent(quantity=100):
    return {
        "execution": {
            "action": "Buy",
            "quantity": quantity,
            "limit_price": 50000,
            "rationale": "Isolated test account only",
            "data_gaps": [],
        }
    }


def approve(store, proposal, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_PAPER_APPROVAL_KEY", "isolated-test-key")
    now = 123456
    signature = hmac.new(
        b"isolated-test-key",
        f"{proposal['id']}|{proposal['preview_hash']}|{now}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return store.approve(
        proposal["id"],
        proposal["preview_hash"],
        {"timestamp": now, "signature": signature},
        now=now,
    )


def test_proposal_does_not_spend_or_submit(tmp_path):
    s = Store(tmp_path)
    p = s.proposal("r1", intent(), bundle(), "HOSE")
    assert p["status"] == "PROPOSED"
    book = s.snapshot()
    assert book["cash"] == 100_000_000 and not book["positions"]
    assert book["orders"][0]["approved_at"] is None
    with pytest.raises(ValueError, match="xác nhận"):
        s.approve(p["id"], p["preview_hash"], {})


def test_human_signature_is_required_and_no_duplicate_approval(tmp_path, monkeypatch):
    s = Store(tmp_path)
    p = s.proposal("r1", intent(), bundle(), "HOSE")
    monkeypatch.setenv("TRADINGAGENTS_PAPER_APPROVAL_KEY", "isolated-test-key")
    with pytest.raises(ValueError):
        s.approve(
            p["id"], p["preview_hash"], {"timestamp": 123456, "signature": "forged"}, now=123456
        )
    approve(s, p, monkeypatch)
    with pytest.raises(ValueError, match="xử lý"):
        approve(s, p, monkeypatch)
    assert s.snapshot()["available_cash"] == 94_992_500
    assert not s.snapshot()["positions"]


def test_pending_buys_count_towards_position_limit(tmp_path, monkeypatch):
    s = Store(tmp_path)
    p = s.proposal("r1", intent(400), bundle(), "HOSE")
    approve(s, p, monkeypatch)
    with pytest.raises(ValueError, match="tỷ trọng"):
        s.proposal("r2", intent(), bundle(), "HOSE")


def test_stale_preview_cannot_be_approved(tmp_path, monkeypatch):
    s = Store(tmp_path)
    p = s.proposal("r1", intent(), bundle(), "HOSE")
    q = s.proposal("r2", intent(), bundle(), "HOSE")
    approve(s, q, monkeypatch)
    with pytest.raises(ValueError, match="thay đổi"):
        approve(s, p, monkeypatch)


def test_actual_backbroker_next_bar_and_settlement(tmp_path, monkeypatch):
    s = Store(tmp_path)
    p = s.proposal("r1", intent(), bundle(), "HOSE")
    approve(s, p, monkeypatch)
    initial = s.advance("2026-10-26", {"FPT.VN": bars()})
    assert not initial["positions"]
    filled = s.advance("2026-10-27", {"FPT.VN": bars()})
    assert filled["orders"][0]["status"] == "FILLED"
    assert filled["orders"][0]["result"]["fill_date"] == "2026-10-27"
    assert filled["positions"]["FPT.VN"]["available_quantity"] == 0
    settled = s.advance("2026-10-29", {"FPT.VN": bars()})
    assert settled["positions"]["FPT.VN"]["available_quantity"] == 100
    assert settled["cash"] == 94_992_500


def test_revised_consumed_bars_do_not_rewrite_trades(tmp_path, monkeypatch):
    s = Store(tmp_path)
    p = s.proposal("r1", intent(), bundle(), "HOSE")
    approve(s, p, monkeypatch)
    before = s.advance("2026-10-27", {"FPT.VN": bars()})
    revised = bars()
    revised[0]["close"] = 50050
    with pytest.raises(ValueError, match="thay đổi"):
        s.advance("2026-10-28", {"FPT.VN": revised})
    assert s.snapshot()["version"] == before["version"]


def test_limit_not_crossed_expires_without_fabricated_fill():
    order = {
        "id": "test",
        "ticker": "FPT.VN",
        "asof": "2026-10-26",
        "side": "Buy",
        "quantity": 100,
        "limit_price": 48000,
    }
    r = replay([order], {"FPT.VN": bars()}, 100_000_000, "2026-10-28")
    assert r["orders"]["test"]["state"] == "EXPIRED" and r["orders"]["test"]["filled_quantity"] == 0
    assert r["cash"] == 100_000_000


def test_low_volume_partial_expires():
    order = {
        "id": "test",
        "ticker": "FPT.VN",
        "asof": "2026-10-26",
        "side": "Buy",
        "quantity": 300,
        "limit_price": 50000,
    }
    rows = bars()
    rows[1]["volume"] = 10000
    r = replay([order], {"FPT.VN": rows}, 100_000_000, "2026-10-29")
    assert r["orders"]["test"]["state"] == "PARTIAL_EXPIRED"
    assert r["orders"]["test"]["filled_quantity"] == 100


def test_halt_does_not_fill():
    order = {
        "id": "test",
        "ticker": "FPT.VN",
        "asof": "2026-10-26",
        "side": "Buy",
        "quantity": 100,
        "limit_price": 50000,
    }
    rows = bars()
    rows[1]["volume"] = 0
    r = replay([order], {"FPT.VN": rows}, 100_000_000, "2026-10-28")
    assert r["orders"]["test"]["filled_quantity"] == 0


@pytest.mark.parametrize("quantity", [True, 100.2, "100"])
def test_execution_requires_actual_integer(quantity):
    with pytest.raises(ValidationError):
        ExecutionIntent(
            action="Buy", quantity=quantity, limit_price=50000, rationale="test", data_gaps=[]
        )


def test_no_short_selling_or_invalid_tick():
    book = {
        "positions": {},
        "available_cash": 100_000_000,
        "nav": 100_000_000,
        "max_position_fraction": 0.2,
        "commission": 0.0015,
    }
    o = {
        "ticker": "FPT.VN",
        "side": "Sell",
        "quantity": 100,
        "exchange": "HOSE",
        "limit_price": 50000,
    }
    with pytest.raises(ValueError, match="Không đủ cổ phiếu"):
        validate_order(o, book)
    o.update(side="Buy", limit_price=50010)
    with pytest.raises(ValueError, match="bước giá"):
        validate_order(o, book)


def test_news_excludes_future_and_undated_and_duplicates():
    raw = {
        "data": {
            "content": [
                {"newsId": 1, "publicDate": "2026-10-29", "newsTitle": "Known"},
                {"newsId": 1, "publicDate": "2026-10-29", "newsTitle": "Duplicate"},
                {"newsId": 2, "publicDate": "2026-11-01", "newsTitle": "Future"},
                {"newsId": 3, "newsTitle": "Unknown date"},
            ]
        }
    }
    assert [
        x["headline"]
        for x in vietnam.normalize_news(
            raw, "2026-10-23", "2026-10-30", "https://example.test/source"
        )
    ] == ["Known"]


def test_exact_request_cache_distinguishes_model_and_preserves_tool_calls(tmp_path):
    class Backend:
        calls = 0

        def invoke(self, value, config):
            self.calls += 1
            return AIMessage(
                content="", tool_calls=[{"id": "call-1", "name": "get_stock_data", "args": {}}]
            )

    backend = Backend()
    r = Runtime({"data_cache_dir": str(tmp_path)})
    spec = {"provider": "test", "model": "a"}
    value = r.prompt("Evidence", "Market Analyst")
    a = r.invoke("Market Analyst", spec, backend, value, {})
    b = r.invoke("Market Analyst", spec, backend, value, {})
    assert a.tool_calls == b.tool_calls and backend.calls == 1
    r.invoke("Market Analyst", {**spec, "model": "b"}, backend, value, {})
    assert backend.calls == 2


def test_settlement_vn_prices_and_original_foreign_path(monkeypatch, tmp_path):
    from tradingagents.memory import settlement

    calls = []
    monkeypatch.setattr(vietnam, "load", lambda ticker, asof, cache_dir, **kw: {"prices": bars()})
    monkeypatch.setattr(settlement, "get_closes", lambda *args: calls.append(args) or "foreign")
    original = settlement.get_closes
    with vietnam.settlement_scope(
        {"data_cache_dir": str(tmp_path), "desktop_vietnam": {"enabled": True}}
    ):
        series = settlement.get_closes("FPT.VN", "2026-10-26", "2026-10-28")
        assert len(series) == 2 and list(series) == [50000, 50000]
        assert settlement.get_closes("AAPL", "2026-10-26", "2026-10-28") == "foreign"
    assert settlement.get_closes is original


def test_backtest_uses_separate_dated_bundle_and_restores_factory(tmp_path, monkeypatch):
    from desktop import integration
    from tradingagents import backtest

    observed = []

    class Original:
        def __init__(self, config):
            self.config = config
            self.resolve_instrument_context = lambda *a: "Original identity"

        def propagate(self, ticker, asof, asset_type="stock", portfolio=None):
            from tradingagents.dataflows import router

            observed.append(
                (
                    asof,
                    self.config["desktop_quant_fingerprint"],
                    self.resolve_instrument_context(ticker),
                )
            )
            assert "50000" in router.VENDOR_METHODS["get_stock_data"]["vietnam"](ticker, asof, asof)
            return {}, "Hold"

    def source(ticker, asof, *args, **kw):
        q = bundle()
        q.update(
            analysis_date=asof,
            company_name="FPT",
            exchange="HOSE",
            currency="VND",
            provider="Isolated test source",
            fingerprint=asof,
            missing=[],
            warnings=[],
            basis=[],
            sources=[],
        )
        q["prices"] = [dict(bars()[0], date=asof)]
        return q

    monkeypatch.setattr(backtest, "TradingAgentsGraph", Original)
    monkeypatch.setattr(vietnam, "load", source)
    monkeypatch.setattr(integration, "apply_role_models", lambda *a, **kw: None)
    cfg = {
        "desktop_vietnam": {"enabled": True},
        "data_cache_dir": str(tmp_path / "cache"),
        "results_dir": str(tmp_path / "results"),
    }
    with integration.backtest_scope(cfg, lambda *a: None):
        graph = backtest.TradingAgentsGraph(config=cfg)
        graph.propagate("FPT.VN", "2026-10-26")
        graph.propagate("FPT.VN", "2026-10-27")
    assert backtest.TradingAgentsGraph is Original
    assert [(d, f) for d, f, _ in observed] == [
        ("2026-10-26", "2026-10-26"),
        ("2026-10-27", "2026-10-27"),
    ]
    assert (tmp_path / "results/evidence/FPT.VN/2026-10-26/integration.json").exists()


def test_cross_ticker_reflection_does_not_receive_current_ticker_quant(tmp_path):
    runtime = Runtime({"data_cache_dir": str(tmp_path)}, quant={"ticker": "FPT.VN"}, book=None)
    prompt = runtime.prompt("Reflect on another ticker only", "Memory Log")
    assert "FPT" not in str(prompt)
    assert "CANONICAL QUANTITATIVE" not in str(prompt)


def test_request_cache_ignores_graph_ids_but_preserves_evidence(tmp_path):
    from langchain_core.messages import HumanMessage

    class Backend:
        calls = 0

        def invoke(self, *a, **kw):
            self.calls += 1
            return AIMessage(content="Report")

    r = Runtime({"data_cache_dir": str(tmp_path)})
    m = Backend()
    r.invoke("Trader", {"model": "a"}, m, [HumanMessage(content="Same evidence", id="run-1")], {})
    r.invoke("Trader", {"model": "a"}, m, [HumanMessage(content="Same evidence", id="run-2")], {})
    assert m.calls == 1
    r.invoke(
        "Trader", {"model": "a"}, m, [HumanMessage(content="Changed evidence", id="run-2")], {}
    )
    assert m.calls == 2


def test_statement_compaction_keeps_all_labelled_values_and_zero(monkeypatch):
    from tradingagents.dataflows import router

    q = bundle()
    q.update(
        analysis_date="2026-10-30",
        statements={
            "income": {
                "fields": {"sales": "Net sales", "cost": "Costs", "empty": "Missing"},
                "quarters": [
                    {
                        "yearReport": 2026,
                        "lengthReport": 3,
                        "publicDate": "2026-10-29",
                        "sales": 123,
                        "cost": 0,
                        "empty": None,
                        "unlabelled_null": None,
                    }
                ],
                "years": [],
            }
        },
        basis=[],
    )
    with vietnam.vendor_scope(q):
        text = router.VENDOR_METHODS["get_income_statement"]["vietnam"]("FPT.VN")
    assert "Net sales [sales],123" in text and "Costs [cost],0" in text
    assert "unlabelled_null" not in text and "Missing [empty]" not in text


@pytest.mark.parametrize(
    "source,timing",
    [
        (
            {
                "name": "Retrieved source",
                "url": "https://example.test/retrieved",
                "retrieved_at": "2026-10-30T08:00:00Z",
            },
            "retrieved 2026-10-30T08:00:00Z",
        ),
        (
            {
                "name": "Observed source",
                "url": "https://example.test/observed",
                "observed_at": "2026-10-30T08:00:00Z",
                "published": "2026-10-08",
            },
            "observed 2026-10-30T08:00:00Z",
        ),
        (
            {"name": "Undated source", "url": "https://example.test/undated"},
            "retrieval/observation time unavailable",
        ),
    ],
)
def test_saved_export_includes_evidence_and_no_execution_from_prose(tmp_path, source, timing):
    from desktop.integration import append_evidence
    from desktop.report_runtime import execution_details
    from tradingagents.reporting import write_report_tree

    state = {
        "trade_date": "2026-10-30",
        "final_rating": "Hold",
        "final_trade_decision": "**Rating**: Hold\nNo executable trade.",
    }
    path = write_report_tree(state, "FPT.VN", tmp_path, html=True)
    (tmp_path / "execution.json").write_text(
        json.dumps(
            execution_details(
                {"llm_provider": "test", "quick_think_llm": "test", "deep_think_llm": "test"}
            )
        )
    )
    result = {
        "quantitative": {
            "ticker": "FPT.VN",
            "quote_date": "2026-10-29",
            "metrics": {
                "close": {
                    "value": 52700,
                    "unit": "VND/share",
                    "asof": "2026-10-29",
                    "formula": "Completed close",
                    "source": "Test source",
                }
            },
            "basis": [],
            "warnings": [],
            "sources": [source],
        },
        "paper_proposal": {"status": "BLOCKED"},
    }
    append_evidence(path, "FPT.VN", state, None, result)
    assert "52700" in path.read_text()
    assert "52700" in path.with_suffix(".html").read_text()
    assert "BLOCKED" in (tmp_path / "7_evidence/evidence.md").read_text()
    assert timing in path.read_text()
    assert timing in path.with_suffix(".html").read_text()


@pytest.mark.parametrize("published", ["", "invalid", "2026-00-01", "2026-11-01", None])
def test_financials_without_valid_past_publication_are_withheld(published):
    assert not vietnam._published({"publicDate": published}, "2026-10-30")


def test_each_downstream_role_receives_same_snapshot_and_explicit_missing(tmp_path):
    from langchain_core.messages import HumanMessage

    quant = {
        "ticker": "FPT.VN",
        "company_name": "FPT",
        "exchange": "HOSE",
        "analysis_date": "2026-10-30",
        "currency": "VND",
        "provider": "Test source",
        "metrics": {"close": {"value": 52700}, "pe_ttm": {"value": None}},
        "missing": ["pe_ttm"],
        "warnings": [],
        "basis": [],
        "fingerprint": "shared-quant-test",
    }

    class Backend:
        def invoke(self, *args, **kwargs):
            return AIMessage(content="Evidence-based report")

    runtime = Runtime({"data_cache_dir": str(tmp_path)}, quant=quant)
    for role in [
        "Bull Researcher",
        "Bear Researcher",
        "Research Manager",
        "Trader",
        "Aggressive Analyst",
        "Conservative Analyst",
        "Neutral Analyst",
    ]:
        runtime.invoke(
            role,
            {"provider": "test", "model": "test"},
            Backend(),
            runtime.prompt([HumanMessage(content="Original role instructions")], role),
            {},
        )
    assert len(runtime.handoffs) == 7
    assert {r["snapshot_fingerprint"] for r in runtime.handoffs.values()} == {"shared-quant-test"}
    assert all(r["missing_metrics"] == ["pe_ttm"] for r in runtime.handoffs.values())


def test_missing_capsule_blocks_false_completion(tmp_path):
    class Backend:
        def invoke(self, *args, **kwargs):
            return AIMessage(content="A polished but ungrounded reply")

    quant = {
        "ticker": "FPT.VN",
        "analysis_date": "2026-10-30",
        "metrics": {"close": {"value": 52700}},
        "fingerprint": "required-context",
    }
    runtime = Runtime({"data_cache_dir": str(tmp_path)}, quant=quant)
    with pytest.raises(ValueError, match="handoff"):
        runtime.invoke("Trader", {}, Backend(), [], {})
    assert "Trader" not in runtime.handoffs
