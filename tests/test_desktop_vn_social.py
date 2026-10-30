"""Public RSS evidence cannot become invented facts or historical knowledge."""

from datetime import datetime, timedelta

import pytest
import requests
from langchain_core.messages import SystemMessage

from desktop import social_vietnam as v
from desktop.grounding import GroundedSentimentReport, ledger, validate

NOW = datetime(2026, 10, 30, 17, tzinfo=v.TZ)


def feed(items=None, channel="https://f247.com/tag/FPT"):
    if items is None:
        items = [
            (
                1,
                "FPT discussion",
                "Thu, 29 Oct 2026 13:00:00 +0000",
                "<p>FPT is a topic, not a verified fact.</p>",
            )
        ]
    xml = "<rss><channel><link>" + channel + "</link>"
    for id_, title, date, body in items:
        xml += f"<item><title>{title}</title><link>https://f247.com/t/topic/{id_}</link><pubDate>{date}</pubDate><description><![CDATA[{body}]]></description><creator>Private profile not needed</creator></item>"
    return (xml + "</channel></rss>").encode()


class Session:
    def __init__(self, raw=None, error=False):
        self.headers = {}
        self.raw = feed() if raw is None else raw
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.error:
            raise requests.ConnectionError("offline")
        raw = self.raw

        class Response:
            content = raw

            def raise_for_status(self):
                pass

        return Response()


def test_records_are_excerpted_tagged_opinions_not_author_profiles(tmp_path):
    q = v.load("FPT.VN", "2026-10-30", tmp_path, session=Session(), now=NOW)
    assert q["status"] == "available" and len(q["records"]) == 1
    r = q["records"][0]
    assert r["relevance"] == "ticker_in_excerpt_or_title"
    assert "sentiment_score" not in q and "author" not in r
    assert not any("creator" in p.read_text() for p in tmp_path.rglob("*.json"))
    assert "RSS first-post excerpt" in r["coverage"]


def test_tag_only_is_not_ticker_relevance_and_footer_contacts_removed():
    body = "<p>Different company opinion a@b.com 0912345678 https://zalo.me/private</p><small>900 participants</small><script>inject()</script>"
    rows, issues, _ = v.parse_feed(
        feed([(1, "Other company", "Thu, 29 Oct 2026 13:00:00 +0000", body)]),
        "FPT",
        "2026-10-30",
        NOW,
    )
    assert not issues and rows[0]["relevance"] == "tag_only_unverified"
    assert not any(
        token in rows[0]["excerpt"]
        for token in ["a@b.com", "0912345678", "900", "inject()", "zalo.me"]
    )


def test_excerpt_and_context_limits_are_explicit_not_silent(tmp_path):
    items = [(i, "FPT title", "Thu, 29 Oct 2026 13:00:00 +0000", "word " * 150) for i in range(12)]
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(feed(items)), now=NOW)
    assert len(q["records"]) == 12 and len(q["context_records"]) == 8
    assert q["context_omitted_count"] == 4
    assert q["records"][0]["excerpt_truncated"] and len(q["records"][0]["excerpt"].split()) == 100
    assert "records" not in v.summary(q) and v.summary(q)["retained_record_count"] == 12


def test_future_and_outside_seven_day_window_excluded(tmp_path):
    items = [
        (1, "FPT old", "Wed, 21 Oct 2026 13:00:00 +0000", "old"),
        (2, "FPT recent", "Fri, 30 Oct 2026 09:00:00 +0000", "recent"),
        (3, "FPT future", "Fri, 30 Oct 2026 12:00:00 +0000", "future"),
    ]
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(feed(items)), now=NOW)
    assert [r["id"] for r in q["records"]] == ["f247:2"]
    assert q["source_item_count"] == 3


@pytest.mark.parametrize(
    "raw",
    [
        b"<html>challenge</html>",
        b"<rss><channel><link>https://f247.com/tag/HPG</link></channel></rss>",
        b'<!DOCTYPE rss [<!ENTITY x "secret">]><rss/>',
    ],
)
def test_wrong_channel_challenge_and_entities_not_valid_empty_sample(tmp_path, raw):
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(raw), now=NOW)
    assert q["status"] == "unavailable" and not q["records"]


def test_empty_valid_feed_is_bounded_empty_not_neutral(tmp_path):
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(feed([])), now=NOW)
    assert q["status"] == "available" and not q["records"]
    assert "not no discussion" in q["warnings"][0]


def test_duplicate_or_bad_date_is_partial_not_full_coverage(tmp_path):
    items = [
        (1, "FPT one", "Thu, 29 Oct 2026 13:00:00 +0000", "one"),
        (1, "FPT duplicate", "Thu, 29 Oct 2026 13:00:00 +0000", "two"),
        (2, "FPT wrong date", "invalid", "three"),
    ]
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(feed(items)), now=NOW)
    assert q["status"] == "partial" and len(q["records"]) == 1
    assert len(q["warnings"]) == 3


def test_cache_and_failed_refresh_keep_original_source_time(tmp_path):
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(), now=NOW)
    fail = Session(error=True)
    warm = v.load("FPT", "2026-10-30", tmp_path, session=fail, now=NOW + timedelta(minutes=10))
    assert not fail.calls and warm["records"] == q["records"]
    stale = v.load("FPT", "2026-10-30", tmp_path, session=fail, now=NOW + timedelta(hours=1))
    assert stale["status"] == "cached_source_error"
    assert stale["retrieved_at"] == q["retrieved_at"]


def test_published_date_does_not_authorize_unobserved_historical_text(tmp_path):
    v.load("FPT", "2026-10-30", tmp_path, session=Session(), now=NOW)
    fail = Session(error=True)
    q = v.load("FPT", "2026-10-29", tmp_path, session=fail, now=NOW)
    assert q["status"] == "withheld" and not q["records"] and not fail.calls
    replay = v.load("FPT", "2026-10-30", tmp_path, session=fail, now=NOW + timedelta(days=1))
    assert replay["records"] and not fail.calls


def context(tmp_path):
    social = v.load("FPT", "2026-10-30", tmp_path, session=Session(), now=NOW)
    q = {
        "ticker": "FPT.VN",
        "analysis_date": "2026-10-30",
        "fingerprint": "canonical",
        "metrics": {},
        "news": [],
        "domestic_social": social,
    }
    messages = [
        SystemMessage(
            content="<start_of_stocktwits>unavailable<end_of_stocktwits><start_of_reddit>unavailable<end_of_reddit>"
        )
    ]
    expected = ledger(q, messages)
    r = GroundedSentimentReport(
        overall_band="Neutral",
        overall_score=5,
        confidence="low",
        narrative="Mẫu diễn đàn không đại diện thị trường.",
        context_readback=expected,
        evidence_claims=[{"claim": "Có một trích đoạn trong mẫu RSS.", "source_ids": ["f247:1"]}],
    )
    return q, messages, r


def test_community_ids_are_verified_and_receipt_discloses_scope(tmp_path):
    q, messages, report = context(tmp_path)
    result, receipt = validate(report, q, messages)
    assert receipt["validated"] and "F247 RSS: present" in result.narrative
    assert receipt["readback"]["domestic_topic_ids"] == ["f247:1"]


@pytest.mark.parametrize("bad", [[], ["f247:wrong"], ["f247:1", "f247:1"]])
def test_shallow_or_made_up_community_readback_rejected(tmp_path, bad):
    q, messages, report = context(tmp_path)
    report.context_readback.domestic_topic_ids = bad
    with pytest.raises(ValueError, match="community topic set"):
        validate(report, q, messages)


def test_community_status_and_unseen_claims_cannot_be_invented(tmp_path):
    q, messages, report = context(tmp_path)
    report.context_readback.domestic_social_status = "empty"
    with pytest.raises(ValueError, match="context check"):
        validate(report, q, messages)
    report.context_readback.domestic_social_status = "present"
    report.evidence_claims[0].source_ids = ["f247:999"]
    with pytest.raises(ValueError, match="outside"):
        validate(report, q, messages)


def test_tag_only_topics_stay_visible_but_cannot_fill_model_context(tmp_path):
    items = [
        (i, "Other company", "Thu, 29 Oct 2026 13:00:00 +0000", "Unrelated discussion")
        for i in range(10)
    ]
    items.append((100, "FPT topic", "Wed, 28 Oct 2026 13:00:00 +0000", "FPT discussion"))
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(feed(items)), now=NOW)
    assert len(q["records"]) == 11 and len(q["context_records"]) == 1
    assert q["context_records"][0]["id"] == "f247:100" and q["context_omitted_count"] == 10


def test_sparse_domestic_sample_cannot_be_hidden_by_available_foreign_feeds(tmp_path):
    q, messages, report = context(tmp_path)
    messages = [
        SystemMessage(
            content="<start_of_stocktwits>observed<end_of_stocktwits><start_of_reddit>observed<end_of_reddit>"
        )
    ]
    q["news"] = [
        {
            "id": str(i),
            "published": "2026-10-29",
            "headline": "Observed headline",
            "url": "https://example.org",
            "coverage": "headline only",
        }
        for i in range(5)
    ]
    report.context_readback = type(report.context_readback)(**ledger(q, messages))
    report.confidence = "high"
    with pytest.raises(ValueError, match="confidence"):
        validate(report, q, messages)


def test_fpt_retail_legal_name_is_not_direct_fpt_company_evidence(tmp_path):
    raw = feed(
        [
            (
                1,
                "FPT Retail",
                "Thu, 29 Oct 2026 13:00:00 +0000",
                "FPT RETAIL (FRT): CTCP Bán lẻ Kỹ thuật số FPT",
            )
        ]
    )
    q = v.load("FPT", "2026-10-30", tmp_path, session=Session(raw), now=NOW)
    assert q["records"][0]["relevance"] == "related_company_or_ambiguous"
    assert not q["context_records"] and q["context_omitted_count"] == 1
