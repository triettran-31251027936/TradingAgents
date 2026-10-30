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
    assert r["status"] == "withheld" and not session.calls
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
