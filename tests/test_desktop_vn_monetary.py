"""Money/credit facts must retain period, scale and historical availability."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from desktop import monetary_vietnam as m
from desktop.macro_vietnam import summary, tool

NOW = datetime(2026, 10, 30, 14, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
CREDIT = "Đến ngày 30/9/2026, dư nợ tín dụng toàn hệ thống đạt 12,34 triệu tỷ đồng, tăng 8,76% so với cuối năm 2025 và tăng 10,12% so với cùng kỳ năm 2025."
DEPOSIT = "Đến ngày 20/9/2026, lãi suất tiền gửi bình quân của các giao dịch phát sinh mới ở mức 5,67%/năm."


def html(body=None, published="08/10/2026"):
    return f'<h1>Ngân hàng Nhà nước họp báo kết quả</h1><div class="date_post_core">{published}</div><div class="entry-content"><p>{body or CREDIT + DEPOSIT}</p></div>'


class Response:
    def __init__(self, url, text):
        self.url, self.text, self.content = url, text, text.encode()

    def raise_for_status(self):
        pass


class Session:
    def __init__(self, body=None, failed=False):
        self.headers = {}
        self.calls = []
        self.body = body
        self.failed = failed

    def get(self, url, **kw):
        self.calls.append(url)
        if self.failed:
            raise requests.ConnectionError("offline")
        return Response(url, "<html>Homepage</html>" if url == m.ROOT else html(self.body))


def test_money_units_and_comparison_bases_are_distinct():
    r = m.parse(html(), m.SEED, NOW)["metrics"]
    assert r["vn_credit_stock"]["value"] == 12.34e15
    assert r["vn_credit_ytd"]["value"] == pytest.approx(0.0876)
    assert r["vn_credit_yoy"]["value"] == pytest.approx(0.1012)
    assert r["vn_new_deposit_rate"]["value"] == pytest.approx(0.0567)
    assert r["vn_credit_stock"]["asof"] == "2026-09-30"
    assert r["vn_new_deposit_rate"]["asof"] == "2026-09-20"
    assert "NOT a policy rate" in r["vn_new_deposit_rate"]["formula"]


def test_conflicting_duplicates_are_withheld_not_last_row_wins():
    r = m.parse(html(CREDIT + CREDIT.replace("12,34", "13,34") + DEPOSIT), m.SEED, NOW)
    assert "vn_credit_stock" in r["conflicts"]
    assert "vn_credit_stock" not in r["metrics"]
    assert r["metrics"]["vn_credit_yoy"]["value"] == pytest.approx(0.1012)


@pytest.mark.parametrize(
    "body",
    [
        CREDIT.replace("toàn hệ thống", "của ngân hàng ví dụ"),
        CREDIT.replace("đạt", "phấn đấu đạt"),
        "Dự kiến " + CREDIT,
        CREDIT.replace("2025", "2024"),
        CREDIT.replace("/2026", "/2027"),
    ],
)
def test_bank_target_wrong_base_or_future_period_is_not_system_fact(body):
    assert not m.parse(html(body), m.SEED, NOW)["metrics"]


def test_publication_and_identity_guard():
    with pytest.raises(ValueError, match="Future"):
        m.parse(html(published="01/11/2026"), m.SEED, NOW)
    with pytest.raises(ValueError, match="Untrusted"):
        m.parse(html(), "https://example.invalid/release", NOW)
    with pytest.raises(ValueError, match="schema"):
        m.parse("<h1>Site redesign</h1>", m.SEED, NOW)


def test_current_load_routes_real_tool_and_archives_source(tmp_path):
    session = Session()
    r = m.load("2026-10-30", tmp_path, now=NOW, session=session)
    assert len(session.calls) == 2
    assert len(list((tmp_path / "vn-macro/monetary").glob("raw_*.json"))) == 1
    result = json.loads(
        tool({"asof": "2026-10-30", "monetary_releases": r}, "vn_credit_yoy", "2026-10-30")
    )
    assert result["status"] == "available" and result["metric"]["value"] == pytest.approx(0.1012)
    again = Session(failed=True)
    assert m.load("2026-10-30", tmp_path, now=NOW, session=again) == r
    assert not again.calls


def test_historical_run_cannot_fetch_current_article_as_old_vintage(tmp_path):
    session = Session()
    r = m.load("2026-10-29", tmp_path, now=NOW, session=session)
    assert not session.calls
    assert all(x["value"] is None for x in r["metrics"].values())
    m.load("2026-10-30", tmp_path, now=NOW, session=session)
    tomorrow = NOW.replace(day=31)
    session = Session(failed=True)
    r = m.load("2026-10-30", tmp_path, now=tomorrow, session=session)
    assert r["metrics"]["vn_credit_ytd"]["value"] == pytest.approx(0.0876)
    assert not session.calls


def test_old_observation_is_withheld_even_when_release_is_recent(tmp_path):
    r = m.load(
        "2026-10-30",
        tmp_path,
        now=NOW,
        session=Session((CREDIT + DEPOSIT).replace("/9/2026", "/6/2026")),
    )
    assert all(x["value"] is None and x["status"] == "stale" for x in r["metrics"].values())


def test_outage_is_missing_not_zero(tmp_path):
    r = m.load("2026-10-30", tmp_path, now=NOW, session=Session(failed=True))
    assert r["warnings"] and not r["releases"]
    assert all(x["value"] is None for x in r["metrics"].values())


def test_cli_summary_does_not_duplicate_canonical_metric_tables(tmp_path):
    data = m.load("2026-10-30", tmp_path, now=NOW, session=Session())
    result = summary({"monetary_releases": data})["monetary_releases"]
    assert "metrics" not in result
    assert all("metrics" not in r for r in result["releases"])
    assert result["sources"] == data["sources"]
    assert data["metrics"]["vn_credit_stock"]["value"] is not None
