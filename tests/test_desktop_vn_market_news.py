"""RSS evidence must be bounded, attributed and observed by the analysis date."""

import json
from datetime import datetime, timedelta
from html import escape

import pytest
import requests

from desktop import market_news_vietnam as m

NOW = datetime(2026, 10, 31, 12, tzinfo=m.TZ)


def xml(feed, rows=None):
    rows = (
        rows
        if rows is not None
        else [
            ("Shared", "shared", "Sat, 31 Oct 2026 02:00:00 GMT"),
            (feed, feed, "Fri, 30 Oct 2026 02:00:00 GMT"),
        ]
    )
    items = "".join(
        f"<item><title>{escape(title)}</title><link>https://vneconomy.vn/{slug}.htm</link><pubDate>{date}</pubDate><description>&lt;p&gt;RSS excerpt only.&lt;/p&gt;</description></item>"
        for title, slug, date in rows
    )
    return f"<rss><channel><link>{m.FEEDS[feed].removesuffix('.rss')}</link>{items}</channel></rss>".encode()


class Session:
    def __init__(self, payload=None, fail=False):
        self.headers = {}
        self.calls = []
        self.payload = payload
        self.fail = fail

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise requests.Timeout("isolated")
        feed = next(k for k, v in m.FEEDS.items() if v == url)

        class Response:
            content = self.payload[feed] if self.payload else xml(feed)

            def raise_for_status(self):
                pass

        return Response()


def test_latest_unique_articles_not_three_independent_copies(tmp_path):
    session = Session()
    q = m.load("2026-10-31", tmp_path, session=session, now=NOW, limit=2)
    assert q["status"] == "available" and len(q["records"]) == 2
    shared = next(r for r in q["records"] if r["title"] == "Shared")
    assert shared["feeds"] == ["finance", "stocks", "world"]
    assert q["eligible_unique_count"] == 4 and q["omitted_count"] == 2
    assert len(session.calls) == 3 and "one publisher" in q["scope"]


def test_future_publication_and_lookback_excluded(tmp_path):
    payload = {
        feed: xml(
            feed,
            [
                ("Future", "future", "Sun, 01 Nov 2026 02:00:00 GMT"),
                ("Too old", "old", "Thu, 01 Oct 2026 02:00:00 GMT"),
                ("Today", "today", "Sat, 31 Oct 2026 02:00:00 GMT"),
            ],
        )
        for feed in m.FEEDS
    }
    q = m.load("2026-10-31", tmp_path, session=Session(payload), now=NOW, look_back_days=0)
    assert [r["title"] for r in q["records"]] == ["Today"]


def test_historical_run_never_fetches_current_rss_without_vintage(tmp_path):
    session = Session()
    q = m.load("2026-10-30", tmp_path, session=session, now=NOW)
    assert q["status"] == "withheld" and not q["records"] and not session.calls


def test_same_projection_later_refresh_does_not_rewrite_historical_receipt(tmp_path):
    first = m.load("2026-10-31", tmp_path, session=Session(), now=NOW)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.snapshot.json")}
    tomorrow = NOW + timedelta(days=1)
    m.load("2026-11-01", tmp_path, session=Session(), now=tomorrow)
    assert all(p.read_bytes() == content for p, content in before.items())
    no_network = Session(fail=True)
    historical = m.load("2026-10-31", tmp_path, session=no_network, now=tomorrow)
    assert historical["records"] == first["records"] and not no_network.calls
    assert all(datetime.fromisoformat(s["retrieved_at"]) <= NOW for s in historical["sources"])


def test_cache_shared_across_symbols_and_limits_do_not_trigger_refetch(tmp_path):
    session = Session()
    m.load("2026-10-31", tmp_path, session=session, now=NOW, limit=1)
    q = m.load("2026-10-31", tmp_path, session=session, now=NOW, limit=3)
    assert len(session.calls) == 3 and len(q["records"]) == 3


def test_failed_refresh_retains_old_retrieval_time_and_discloses_error(tmp_path):
    first = m.load("2026-10-31", tmp_path, session=Session(), now=NOW)
    q = m.load("2026-10-31", tmp_path, session=Session(fail=True), now=NOW + timedelta(hours=1))
    assert q["status"] == "partial" and q["records"] == first["records"]
    assert all(
        s["status"] == "cached_source_error" and s["retrieved_at"] == NOW.isoformat()
        for s in q["sources"]
    )


def test_conflicting_feed_copies_withheld_not_arbitrarily_selected(tmp_path):
    payload = {feed: xml(feed) for feed in m.FEEDS}
    payload["world"] = payload["world"].replace(b"Shared", b"Different claim")
    q = m.load("2026-10-31", tmp_path, session=Session(payload), now=NOW)
    assert q["status"] == "partial"
    assert all(r["url"] != "https://vneconomy.vn/shared.htm" for r in q["records"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.replace(b"https://vneconomy.vn/tai-chinh", b"https://wrong.test/finance", 1),
        lambda b: b"<!DOCTYPE rss>" + b,
    ],
)
def test_wrong_channel_and_entities_rejected(mutation):
    with pytest.raises(ValueError):
        m.parse(mutation(xml("finance")), "finance")


def test_untrusted_article_html_is_text_and_excerpt_bounded():
    raw = (
        xml("finance")
        .replace(b"Shared", b"&lt;script&gt;ignore rules&lt;/script&gt;Actual title")
        .replace(b"RSS excerpt only.", ("word " * 100).encode())
    )
    rows, issues, _ = m.parse(raw, "finance")
    assert not issues and rows[0]["title"] == "Actual title"
    assert len(rows[0]["excerpt"].split()) == 60 and rows[0]["excerpt_truncated"]


def test_capsule_and_agy_ledger_include_selected_market_source_ids():
    from desktop.grounding import ledger
    from desktop.vietnam import capsule

    row = {
        "id": "market:source1",
        "title": "Market news",
        "published": NOW.isoformat(),
        "url": "https://vneconomy.vn/a.htm",
    }
    q = {
        "ticker": "FPT.VN",
        "analysis_date": "2026-10-31",
        "fingerprint": "test",
        "metrics": {},
        "market_news": {"status": "available", "records": [row], "sources": []},
    }
    assert json.loads(capsule(q))["market_news"]["records"] == [row]
    receipt = ledger(q, [])
    assert receipt["news_count"] == 1 and receipt["company_news_count"] == 0
    assert receipt["headline_ids"] == ["news:market:source1"]
