from datetime import date, datetime

import pytest

from desktop.cash_dividends import metrics, parse_notice
from desktop.vietnam import TZ


NOTICE = """
<h3 class="title-category">VHM: Chi trả cổ tức bằng tiền</h3>
<div class="time-newstcph">Cập nhật ngày 19/06/2026 - 09:42:40</div>
<div class="content-category">
Mã chứng khoán: VHM
Mã ISIN: VN000000VHM0
Ngày đăng ký cuối cùng: 30/06/2026
Tỷ lệ thực hiện: 60%/cổ phiếu (01 cổ phiếu được nhận 6.000 đồng)
Ngày thanh toán: 22/07/2026
</div>
"""


def test_cash_notice_requires_published_matching_entitlement():
    record_date = date(2026, 6, 30)
    cutoff = datetime(2026, 7, 1, tzinfo=TZ)
    row = parse_notice(NOTICE, "VHM", "VN000000VHM0", record_date, cutoff)
    assert row["cash_vnd_per_eligible_share"] == 6000
    assert row["scheduled_payment_date"] == "2026-07-22"
    assert parse_notice(NOTICE, "VHM", "VN000000VHM0", record_date, datetime(2026, 6, 19, tzinfo=TZ)) is None
    for invalid in [
        NOTICE.replace("VN000000VHM0", "VN000000BSR8"),
        NOTICE.replace("30/06/2026", "29/06/2026"),
        NOTICE.replace("bằng tiền", "bằng cổ phiếu"),
        NOTICE.replace("6.000 đồng", "6.000 đồng; 01 cổ phiếu được nhận 200 đồng"),
    ]:
        with pytest.raises(ValueError):
            parse_notice(invalid, "VHM", "VN000000VHM0", record_date, cutoff)

    row["url"] = "https://vsdc.vn/vi/ad/197086"
    data = {"records": [row], "asof": "2026-07-01", "status": "available"}
    key = "latest_cash_dividend_per_eligible_share"
    assert metrics(data)[key]["value"] == 6000
    assert metrics({**data, "status": "partial"})[key]["value"] is None
    assert metrics({**data, "records": []})[key]["value"] is None


def test_rights_listing_filters_dates_without_assuming_sort_order(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from desktop import cash_dividends

    page = """
    <div id="divlistArticlesTCPH_THQ"><table><tbody>
      <tr><td>1</td><td>01/05/2024</td><td><a href="/ad/123">VHM: Cổ tức bằng tiền</a></td></tr>
      <tr><td>2</td><td>30/06/2026</td><td><a href="/ad/197086">VHM: Chi trả cổ tức bằng tiền</a></td></tr>
      <tr><td>3</td><td>01/06/2026</td><td><a href="/ad/124">VHM: Chi trả cổ tức bằng cổ phiếu</a></td></tr>
    </tbody></table></div>
    """
    monkeypatch.setattr(cash_dividends, "fetch_registry", lambda *_: ("https://vsdc.vn/vi/s-detail/6951", page))
    monkeypatch.setattr(cash_dividends, "parse", lambda *_: {"isin": "VN000000VHM0"})
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(
            url=url, content=NOTICE.encode(), text=NOTICE, raise_for_status=lambda: None,
        )

    observed = datetime(2026, 7, 1, 10, tzinfo=TZ)
    data = cash_dividends.load("VHM", "2026-07-01", tmp_path, now=observed, session=SimpleNamespace(get=get))
    assert calls == ["https://vsdc.vn/ad/197086"]
    assert data["status"] == "available"
    assert data["records"][0]["cash_vnd_per_eligible_share"] == 6000


def test_trailing_yield_requires_stable_share_count_and_same_day_anchored_close():
    from desktop.cash_dividends import trailing_yield

    notice = {"record_date": "2026-06-30", "scheduled_payment_date": "2026-07-22",
              "cash_vnd_per_eligible_share": 300}
    data = {"asof": "2026-11-11", "status": "available", "records": [notice]}
    registry = {"registration_history": [{"date": "2025-11-13", "change": 1_000_000},
                                         {"date": "2026-08-01", "change": 0}]}
    proof = {"verified": True}
    close = {"status": "anchored", "date": "2026-11-11", "observed_close_vnd": 25_300}
    assert trailing_yield(data, registry, proof, close)["value"] == pytest.approx(300 / 25_300)
    assert trailing_yield(data, registry, proof, {**close, "date": "2026-11-10"})["value"] is None
    assert trailing_yield(data, registry, {}, close)["value"] is None
    assert trailing_yield({**data, "records": [notice, notice]}, registry, proof, close)["value"] is None
    registry["registration_history"][1]["change"] = 1_000_000
    withheld = trailing_yield(data, registry, proof, close)
    assert withheld["value"] is None and "do not match" in withheld["formula"]


def test_stock_dividend_rebases_only_when_registered_change_matches_notice():
    from desktop.cash_dividends import trailing_yield

    raw = NOTICE.replace("bằng tiền", "bằng cổ phiếu").replace(
        "Tỷ lệ thực hiện: 60%/cổ phiếu (01 cổ phiếu được nhận 6.000 đồng)",
        "Tỷ lệ thực hiện: 1:1 (Người sở hữu 01 cổ phiếu được nhận 01 cổ phiếu mới)",
    ).replace("30/06/2026", "07/08/2026")
    cutoff = datetime(2026, 11, 10, 23, tzinfo=TZ)
    notice = parse_notice(raw, "VHM", "VN000000VHM0", date(2026, 8, 7), cutoff, stock=True)
    assert (notice["held"], notice["issued"]) == (1, 1)
    event = {"date": "2026-08-25", "change": 4_107_412_004, "reason": "Trả cổ tức bằng cổ phiếu"}
    balance = {"notice_registration_change": event, "notice_published": "2026-08-25T15:24:39+07:00"}
    rights = {"records": [notice], "balance_notice": balance}
    cash = {"record_date": "2026-06-30", "scheduled_payment_date": "2026-07-22",
            "cash_vnd_per_eligible_share": 6000}
    data = {"asof": "2026-11-10", "status": "available", "records": [cash], "stock_rights": rights}
    registry = {"registration_history": [event], "registered_shares": 8_214_824_008}
    close = {"status": "anchored", "date": "2026-11-10", "observed_close_vnd": 62_000}
    metric = trailing_yield(data, registry, {"verified": True}, close)
    assert metric["value"] == pytest.approx(3000 / 62000)
    assert "3000 VND/share" in metric["formula"]
    for corrupt in [
        {"records": [notice]},
        {**rights, "records": [{**notice, "issued": 2}]},
        {**rights, "balance_notice": {**balance, "notice_published": "2026-11-12T00:00:00+07:00"}},
    ]:
        assert trailing_yield({**data, "stock_rights": corrupt}, registry, {"verified": True}, close)["value"] is None
    transition_cash = {**cash, "record_date": "2026-08-10"}
    assert trailing_yield({**data, "records": [cash, transition_cash]}, registry, {"verified": True}, close)["value"] is None
