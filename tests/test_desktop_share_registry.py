"""Registered shares require reconciliation, identity and an observed vintage."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from desktop import share_registry as m

NOW = datetime(2026, 10, 30, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))


def page(ticker="AAA", total="1.200", change="200", last="01/09/2026"):
    fields = {
        "Mã chứng khoán:": ticker,
        "Mã ISIN:": "VN000000AAA0",
        "Loại chứng khoán:": "Cổ phiếu",
        "Mệnh giá:": "10.000 đồng",
        "Nơi giao dịch (*) :": "HOSE",
        "Tổng số chứng khoán đăng ký:": total + " Cổ phiếu",
    }
    body = "".join(
        f'<div class="row"><div class="item-info">{k}</div><div class="item-info item-info-main">{v}</div></div>'
        for k, v in fields.items()
    )
    return (
        '<meta name="__VPToken" content="public-csrf-not-to-archive">'
        + body
        + "<table><tr>"
        + "".join(
            "<th>" + h + "</th>"
            for h in ["STT", "Lần đăng ký", "Lý do", "Số lượng", "Số GCNĐKCK", "Ngày"]
        )
        + "</tr><tr><td>1</td><td></td><td>Đăng ký lần đầu</td><td>1.000</td><td>N/A</td><td>01/01/2025</td></tr>"
        + f'<tr><td>2</td><td>Điều chỉnh</td><td>Thay đổi</td><td>{change}</td><td>N/A</td><td>{last}</td></tr><tr><td colspan="3">Tổng cộng:</td><td>{total}</td><td></td></tr></table>'
    )


class Response:
    def __init__(self, url, text):
        self.url, self.text, self.content = url, text, text.encode()

    def raise_for_status(self):
        pass


class Session:
    def __init__(self, failed=False, duplicate=False):
        self.headers = {}
        self.calls = []
        self.failed = failed
        self.duplicate = duplicate

    def get(self, url, **kw):
        self.calls.append(url)
        if self.failed:
            raise requests.ConnectionError("offline")
        return Response(url, page())

    def post(self, url, **kw):
        self.calls.append(url)
        assert kw["json"] == {"text": "AAA", "type": "5"}
        row = {"href": "/vi/s-detail/12", "content": "AAA: Cổ phiếu Công ty ví dụ"}
        return Response(
            url,
            json.dumps(
                {
                    "success": 0,
                    "data": [{"href": "/vi/s-detail/99", "content": "CAAA2601: Chứng quyền"}, row]
                    + ([row] if self.duplicate else []),
                }
            ),
        )


def test_registry_history_sums_to_current_count_and_is_not_outstanding():
    r = m.parse(page(), "AAA", "2026-10-30")
    assert r["registered_shares"] == 1200 and r["par_value_vnd"] == 10000
    assert r["latest_registration_date"] == "2026-09-01" and r["history_reconciled"]
    assert r["exchange"] == "HOSE"
    assert "outstanding_shares" not in r


@pytest.mark.parametrize(
    "html",
    [
        page(ticker="BBB"),
        page(total="1.201"),
        page(change="201"),
        page(last="01/11/2026"),
        page().replace("Cổ phiếu</div>", "Chứng quyền</div>"),
        page().replace("<td>2</td>", "<td>3</td>"),
    ],
)
def test_mismatched_identity_totals_instrument_or_future_events_refused(html):
    with pytest.raises(ValueError):
        m.parse(html, "AAA", "2026-10-30")


def test_explicit_negative_adjustment_is_preserved():
    r = m.parse(page(total="900", change="-100"), "AAA", "2026-10-30")
    assert r["registered_shares"] == 900 and r["registration_history"][-1]["change"] == -100


def test_lookup_uses_exact_stock_and_archives_no_session_token(tmp_path):
    session = Session()
    r = m.load("AAA.VN", "2026-10-30", tmp_path, now=NOW, session=session)
    assert r["status"] == "available" and r["source_url"].endswith("/12")
    assert len(session.calls) == 3
    raw = next((tmp_path / "vn-registry/AAA").glob("raw_*.json")).read_text()
    assert "__VPToken" not in raw and "public-csrf-not-to-archive" not in raw
    assert m.load("AAA", "2026-10-30", tmp_path, now=NOW, session=Session(failed=True)) == r


def test_ambiguous_exact_search_does_not_choose_first(tmp_path):
    r = m.load("AAA", "2026-10-30", tmp_path, now=NOW, session=Session(duplicate=True))
    assert r["status"] == "unavailable"


def test_current_page_cannot_be_backdated_without_observed_snapshot(tmp_path):
    session = Session()
    r = m.load("AAA", "2026-10-29", tmp_path, now=NOW, session=session)
    assert r["status"] == "withheld" and len(session.calls) == 3
    m.load("AAA", "2026-10-30", tmp_path, now=NOW, session=session)
    session = Session(failed=True)
    r = m.load("AAA", "2026-10-30", tmp_path, now=NOW.replace(day=31), session=session)
    assert r["registered_shares"] == 1200 and not session.calls


def test_failed_refresh_is_not_fresh_verified_registration(tmp_path):
    m.load("AAA", "2026-10-30", tmp_path, now=NOW, session=Session())
    r = m.load(
        "AAA", "2026-10-30", tmp_path, now=NOW.replace(hour=22), session=Session(failed=True)
    )
    assert r["status"] == "cached_source_error"


def ipo_page():
    # Source-shaped registration history: auction tranche, whole company after
    # equitization, later stock dividend. These are not all additive quantities.
    raw = page(total="1.200", change="1.000")
    raw = raw.replace("Đăng ký lần đầu", "Cấp Giấy chứng nhận đăng ký cổ phiếu trúng đấu giá")
    raw = raw.replace(
        "<td>1.000</td><td>N/A</td><td>01/01/2025", "<td>100</td><td>N/A</td><td>01/01/2025"
    )
    raw = raw.replace("<td>Thay đổi</td>", "<td>Đăng ký sau khi cổ phần hóa</td>")
    row = "<tr><td>3</td><td></td><td>Trả cổ tức bằng cổ phiếu</td><td>200</td><td>N/A</td><td>01/10/2026</td></tr>"
    return raw.replace('<tr><td colspan="3">Tổng cộng:', row + '<tr><td colspan="3">Tổng cộng:')


def test_ipo_tranche_is_not_added_again_to_whole_company_registration():
    r = m.parse(ipo_page(), "AAA", "2026-10-30")
    assert r["registered_shares"] == 1200 and r["history_reconciled"]
    history = r["registration_history"]
    assert [h["reported_quantity"] for h in history] == [100, 1000, 200]
    assert [h["change"] for h in history] == [None, None, 200]
    assert history[1]["quantity_basis"] == "whole_company_registration"
    assert r["history_reconciliation"]["reported_quantity_sum"] == 1300
    assert (
        r["history_reconciliation"]["method"]
        == "ipo_tranche_replaced_by_full_post_equitization_registration"
    )


@pytest.mark.parametrize(
    "wrong",
    [
        ("cổ phiếu trúng đấu giá", "cổ phiếu phát hành thêm"),
        ("Đăng ký sau khi cổ phần hóa", "Đăng ký bổ sung"),
        ("<td>200</td>", "<td>201</td>"),
        ("01/01/2025", "01/10/2026"),
    ],
)
def test_arbitrary_non_additive_or_misdated_histories_still_withheld(wrong):
    with pytest.raises(ValueError):
        m.parse(ipo_page().replace(*wrong), "AAA", "2026-10-30")


def test_transfer_cutoff_uses_publication_and_effective_dates(tmp_path):
    def notice_row(number, day):
        return (
            f'<li><h3><a href="/ad/{number}">AAA: Chuyển quyền sở hữu</a></h3>'
            f'<div class="time-news">Cập nhật ngày {day} - 09:00:00</div></li>'
        )

    class TransferSession(Session):
        def get(self, url, **kw):
            self.calls.append(url)
            if url.endswith("/ad/20"):
                return Response(
                    url,
                    '<h3 class="title-category">AAA: Chuyển quyền sở hữu 100 cổ phiếu</h3>'
                    '<div class="time-newstcph">Cập nhật ngày 28/10/2026 - 09:00:00</div>'
                    '<div class="content-category">Thông báo thực hiện chuyển quyền sở hữu '
                    '100 cổ phiếu AAA. Ngày hiệu lực chuyển quyền: 27/10/2026.</div>',
                )
            return Response(
                url, page() + '<div id="divlistArticlesTCPH_TCDK"><ul class="list-news">'
                + notice_row(21, "30/10/2026") + notice_row(20, "28/10/2026")
                + '</ul><span id="next">Next</span></div>',
            )

        def post(self, url, **kw):
            if url == m.SEARCH:
                return super().post(url, **kw)
            self.calls.append(url)
            assert kw["json"] == {"SearchKey": "12", "CurrentPage": 2, "RecordOnPage": 10}
            return Response(url, '<ul class="list-news">' + notice_row(19, "27/10/2025") + '</ul>')

    session = TransferSession()
    result = m.load_transfers("AAA", "2026-10-29", tmp_path, now=NOW, session=session)
    assert result["status"] == "available"
    row, = result["records"]
    assert row["shares"] == 100 and row["effective_date"] == "2026-10-27"
    assert row["published"].startswith("2026-10-28T09:00:00")
    assert row["url"].endswith("/20") and not any(url.endswith("/21") for url in session.calls)
    assert "Ownership transfers are separate" in result["scope"]
    offline = Session(failed=True)
    assert m.load_transfers("AAA", "2026-10-29", tmp_path, now=NOW, session=offline) == result
    assert not offline.calls


def test_historical_registry_requires_dated_notice_matching_the_current_history(tmp_path):
    title = "AAA: Điều chỉnh thông tin số lượng cổ phiếu đăng ký"
    stamp = "Cập nhật ngày 01/09/2026 - 09:00:00"

    class NoticeSession(Session):
        def get(self, url, **kw):
            self.calls.append(url)
            if url.endswith("/ad/42"):
                return Response(
                    url,
                    f'<h3 class="title-category">{title}</h3>'
                    f'<div class="time-newstcph">{stamp}</div>'
                    + page().split("<table>")[0].replace(
                        "Tổng số chứng khoán đăng ký:", "Số lượng chứng khoán hiện tại:"
                    ),
                )
            return Response(
                url, page() + '<div id="divlistArticlesTCPH_TCDK"><ul class="list-news">'
                + f'<li><h3><a href="/ad/42">{title}</a></h3><div class="time-news">{stamp}</div></li>'
                + '</ul></div>',
            )

    result = m.load("AAA", "2026-10-29", tmp_path, now=NOW, session=NoticeSession())
    assert result["status"] == "available" and result["source_basis"] == "dated_registration_notice"
    assert result["registered_shares"] == 1200 and result["notice_url"].endswith("/42")
    assert result["notice_published"].startswith("2026-09-01")
    earlier = NoticeSession()
    blocked = m.load("AAA", "2026-08-31", tmp_path, now=NOW, session=earlier)
    assert blocked["status"] == "withheld" and not any(x.endswith("/42") for x in earlier.calls)
