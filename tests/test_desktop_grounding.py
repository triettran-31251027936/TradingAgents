"""Reject shallow or mismatched Antigravity context before it enters the debate."""

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from desktop.grounding import EvidenceClaim, GroundedSentimentReport, ledger, validate
from desktop.model_routing import RoleModel
from desktop.runtime_models import Runtime


def evidence():
    return {
        "ticker": "FPT.VN",
        "company_name": "FPT",
        "exchange": "HOSE",
        "currency": "VND",
        "provider": "Test source",
        "missing": [],
        "warnings": [],
        "basis": [],
        "analysis_date": "2026-10-30",
        "fingerprint": "verified-snapshot-1",
        "metrics": {"close": {"value": 52700}},
        "news": [
            {
                "id": 1,
                "published": "2026-10-29",
                "headline": "A supplied headline",
                "url": "https://example.test/1",
                "coverage": "headline only",
            }
        ],
    }


def source():
    return [
        SystemMessage(
            content="News headlines — Yahoo Finance\n<start_of_stocktwits><stocktwits unavailable: HTTPError><end_of_stocktwits>\n<start_of_reddit><Reddit unavailable: fetch failed><end_of_reddit>"
        )
    ]


def test_agy_must_read_back_new_market_news_ids_too():
    q = evidence()
    q["market_news"] = {
        "records": [
            {
                "id": "market:test",
                "title": "Market headline",
                "url": "https://vneconomy.vn/test.htm",
                "published": "2026-10-30T08:00:00+07:00",
            }
        ]
    }
    complete = report(q)
    validate(complete, q, source())
    complete.context_readback.headline_ids.remove("news:market:test")
    with pytest.raises(ValueError, match="exact provided news set"):
        validate(complete, q, source())


def report(q=None):
    q = q or evidence()
    expected = ledger(q, source())
    return GroundedSentimentReport(
        overall_band="Neutral",
        overall_score=5,
        confidence="low",
        narrative="Chỉ có tiêu đề; không suy ra tâm lý cộng đồng.",
        context_readback={
            k: expected[k]
            for k in (
                "ticker",
                "analysis_date",
                "snapshot_fingerprint",
                "headline_ids",
                "news_count",
                "stocktwits_status",
                "reddit_status",
            )
        },
        evidence_claims=[
            {"claim": "Nguồn cung cấp một tiêu đề, chưa có toàn văn.", "source_ids": ["news:1"]}
        ],
    )


def test_verified_readback_and_receipts_are_visible():
    result, receipt = validate(report(), evidence(), source())
    assert receipt["validated"] and "Kiểm tra nguồn đầu vào" in result.narrative
    assert "news:1" in result.narrative


@pytest.mark.parametrize(
    "text,valid",
    [
        ("P/E 11,3 lần; ROE 18,03%", True),
        ("P/E 12,3 lần", False),
        ("P/E 11,3%", False),
        ("ROE 18,03", False),
        ("EPS TTM 5000", False),
    ],
)
def test_canonical_valuation_readback_requires_matching_figures_and_units(text, valid):
    q = evidence()
    q["metrics"].update(
        pe_ttm={"value": 11.295, "unit": "ratio"}, roe_ttm={"value": 0.1802775, "unit": "fraction"}
    )
    r = report(q)
    r.narrative = text
    r.evidence_claims.append(
        EvidenceClaim(claim=text, source_ids=["metric:pe_ttm", "metric:roe_ttm"])
    )
    if valid:
        assert validate(r, q, source())[1]["validated"]
    else:
        with pytest.raises(ValueError, match="unsupported valuation"):
            validate(r, q, source())


@pytest.mark.parametrize(
    "field,value",
    [
        ("ticker", "VNM.VN"),
        ("analysis_date", "2026-10-29"),
        ("snapshot_fingerprint", "another-snapshot"),
        ("news_count", 99),
        ("stocktwits_status", "present"),
    ],
)
def test_mismatched_context_cannot_complete(field, value):
    r = report()
    r.context_readback = r.context_readback.model_copy(update={field: value})
    with pytest.raises(ValueError, match="context check"):
        validate(r, evidence(), source())


@pytest.mark.parametrize("ids", [[], ["news:2"], ["news:1", "news:1"]])
def test_exact_source_set_is_required(ids):
    r = report()
    r.context_readback.headline_ids = ids
    with pytest.raises(ValueError, match="news set"):
        validate(r, evidence(), source())


def test_citing_unknown_source_is_rejected():
    r = report()
    r.evidence_claims[0].source_ids = ["news:invented"]
    with pytest.raises(ValueError, match="outside"):
        validate(r, evidence(), source())


def test_sparse_sources_cannot_claim_high_confidence():
    r = report()
    r.confidence = "high"
    with pytest.raises(ValueError, match="confidence"):
        validate(r, evidence(), source())


def test_unsupported_valuation_is_rejected():
    r = report()
    r.narrative = "P/E 9,9 lần cho thấy cổ phiếu rẻ."
    with pytest.raises(ValueError, match="valuation"):
        validate(r, evidence(), source())


def test_wrong_context_is_not_cached(tmp_path):
    class Backend:
        def invoke(self, *a, **kw):
            r = report()
            r.context_readback.ticker = "Wrong"
            return r

    cfg = {
        "data_cache_dir": str(tmp_path),
        "desktop_role_models": {
            "Sentiment Analyst": {"provider": "agy_cli", "model": "gemini-3.8-flash-high"}
        },
    }
    runtime = Runtime(cfg, quant=evidence())
    with pytest.raises(ValueError):
        runtime.invoke(
            "Sentiment Analyst",
            cfg["desktop_role_models"]["Sentiment Analyst"],
            Backend(),
            source(),
            {},
            schema=GroundedSentimentReport,
        )
    assert not list(tmp_path.rglob("*.json"))


def test_free_text_fallback_still_requires_grounded_schema(tmp_path):
    class Backend:
        schema = None

        def with_structured_output(self, schema, **kw):
            self.schema = schema
            return self

        def invoke(self, *a, **kw):
            return report()

    cfg = {
        "data_cache_dir": str(tmp_path),
        "desktop_role_models": {
            "Sentiment Analyst": {"provider": "agy_cli", "model": "gemini-3.8-flash-high"}
        },
    }
    runtime = Runtime(cfg, quant=evidence())
    backend = Backend()
    model = RoleModel(
        backend,
        {},
        forced_role="Sentiment Analyst",
        runtime=runtime,
        specs=cfg["desktop_role_models"],
    )
    result = model.invoke(source())
    assert backend.schema is GroundedSentimentReport
    assert isinstance(result, AIMessage) and "Kiểm tra nguồn đầu vào" in result.content
    assert "Yahoo Finance" not in str(runtime.prompt(source(), "Sentiment Analyst"))


def test_unverified_plain_reply_cannot_bypass_guard(tmp_path):
    class Backend:
        def with_structured_output(self, *a, **kw):
            return self

        def invoke(self, *a, **kw):
            return AIMessage(content="Trust this polished report without sources.")

    cfg = {"data_cache_dir": str(tmp_path), "llm_provider": "agy_cli"}
    model = RoleModel(
        Backend(), {}, forced_role="Sentiment Analyst", runtime=Runtime(cfg, quant=evidence())
    )
    with pytest.raises(ValueError, match="receipt"):
        model.invoke(source())
