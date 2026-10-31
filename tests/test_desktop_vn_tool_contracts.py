"""VN transports must honor original tool parameters and disclose feed bounds."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from desktop import vietnam


def article(number, **extra):
    return {
        "newsId": number,
        "publicDate": "2026-10-29T10:00:00",
        "newsTitle": f"Article {number}",
        **extra,
    }


def test_configured_news_count_pages_without_offset_gap():
    calls = []

    def fetch(name, url):
        query = parse_qs(urlparse(url).query)
        page, size = int(query["page"][0]), int(query["size"][0])
        calls.append((page, size))
        return {
            "data": {
                "content": [article(i) for i in range(page * size, (page + 1) * size)],
                "last": False,
            }
        }

    rows, coverage = vietnam.collect_news(fetch, "FPT", "2026-10-23", "2026-10-30", 45)
    assert calls == [(0, 30), (1, 30)]
    assert len(rows) == 45 and len({r["id"] for r in rows}) == 45
    assert coverage["limit_reached"] and not coverage["provider_last_page_reached"]
    assert coverage["raw_rows_received"] == 60


def test_partial_news_page_failure_preserves_received_items_and_gap():
    def fetch(name, url):
        return (
            {"data": {"content": [article(i) for i in range(30)], "last": False}}
            if name == "news"
            else {}
        )

    rows, coverage = vietnam.collect_news(fetch, "FPT", "2026-10-23", "2026-10-30", 45)
    assert len(rows) == 30 and coverage["status"] == "partial"
    assert not coverage["provider_last_page_reached"]


def test_empty_news_and_failed_source_are_distinct():
    _, empty = vietnam.collect_news(
        lambda *a: {"data": {"content": [], "last": True}}, "FPT", "2026-10-23", "2026-10-30", 20
    )
    _, error = vietnam.collect_news(lambda *a: {}, "FPT", "2026-10-23", "2026-10-30", 20)
    assert empty["status"] == "empty_sample" and error["status"] == "unavailable"


def test_news_without_provider_ids_is_not_collapsed_to_one_item():
    raw = {
        "data": {
            "content": [
                article(None, newsTitle="First"),
                article(None, newsTitle="Second"),
                article(None, newsTitle="First"),
            ]
        }
    }
    rows = vietnam.normalize_news(raw, "2026-10-23", "2026-10-30", "https://example.test")
    assert [r["headline"] for r in rows] == ["First", "Second"]


@pytest.fixture
def tool_bundle():
    rows = [
        {"yearReport": 2026, "lengthReport": 1, "publicDate": "2026-04-25", "profit": 100},
        {"yearReport": 2026, "lengthReport": 2, "publicDate": "2026-08-19", "profit": 200},
    ]
    return {
        "ticker": "FPT.VN",
        "analysis_date": "2026-10-30",
        "statements": {
            kind: {
                "fields": {"profit": "Profit"},
                "quarters": rows,
                "years": [
                    {
                        "yearReport": 2025,
                        "lengthReport": 5,
                        "publicDate": "2026-03-30",
                        "profit": 300,
                    }
                ],
            }
            for kind in ("income", "balance", "cash")
        },
        "news": [article(1) | {"id": 1, "published": "2026-10-29", "headline": "Article 1"}],
        "news_coverage": {
            "start_date": "2026-10-23",
            "end_date": "2026-10-30",
            "article_limit": 20,
            "status": "available",
        },
    }


@pytest.mark.parametrize("method", ["get_income_statement", "get_balance_sheet", "get_cashflow"])
def test_statement_requested_date_filters_later_publication(tool_bundle, method):
    from tradingagents.dataflows import router

    with vietnam.vendor_scope(tool_bundle):
        read = router.VENDOR_METHODS[method]["vietnam"]
        text = read("FPT.VN", "quarterly", "2026-05-01")
        assert "2026-Q1 public:2026-04-25" in text
        assert "2026-Q2" not in text and "200" not in text
        assert "2025-FY" in read("FPT.VN", "annual", "2026-05-01")
        with pytest.raises(ValueError, match="after"):
            read("FPT.VN", "quarterly", "2026-11-01")
        with pytest.raises(ValueError, match="frequency"):
            read("FPT.VN", "monthly", "2026-05-01")


def test_news_outside_snapshot_has_explicit_coverage_gap(tool_bundle):
    from tradingagents.dataflows import router

    with vietnam.vendor_scope(tool_bundle):
        read = router.VENDOR_METHODS["get_news"]["vietnam"]
        partial = json.loads(read("FPT.VN", "2026-10-01", "2026-10-30"))
        assert not partial["requested_window_within_snapshot"] and partial["window_warning"]
        assert len(partial["items"]) == 1
        covered = json.loads(read("FPT.VN", "2026-10-25", "2026-10-30"))
        assert covered["requested_window_within_snapshot"] and covered["window_warning"] is None
        with pytest.raises(ValueError, match="after"):
            read("FPT.VN", "2026-10-25", "2026-11-01")


def test_fundamentals_cannot_relabel_current_snapshot_as_earlier_date(tool_bundle):
    from tradingagents.dataflows import router

    with vietnam.vendor_scope(tool_bundle):
        read = router.VENDOR_METHODS["get_fundamentals"]["vietnam"]
        with pytest.raises(ValueError, match="separate dated snapshot"):
            read("FPT.VN", "2026-05-01")
        assert '"analysis_date": "2026-10-30"' in read("FPT.VN", "2026-10-30")


def test_desktop_preparation_passes_configured_news_limit(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from desktop import integration

    supplied = []
    q = {
        "ticker": "FPT.VN",
        "company_name": "FPT",
        "exchange": "HOSE",
        "fingerprint": "isolated",
        "metrics": {},
        "prices": [{"date": "2026-10-30"}],
        "missing": [],
    }

    def load(*args, **kwargs):
        supplied.append(kwargs["news_article_limit"])
        return q

    monkeypatch.setattr(vietnam, "load", load)
    monkeypatch.setattr(integration, "apply_role_models", lambda *a, **kw: None)
    cfg = {
        "desktop_vietnam": {"enabled": True},
        "data_cache_dir": str(tmp_path),
        "news_article_limit": 45,
    }
    integration.prepare(
        SimpleNamespace(config={}), "FPT.VN", "2026-10-30", None, cfg, [], lambda *a: None
    )
    assert supplied == [45]
