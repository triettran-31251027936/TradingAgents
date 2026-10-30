"""Professional dealing data only; no network, paid inference or real orders."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests

from desktop import insider_vietnam as v

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 30, 18, tzinfo=TZ)


def ms(value):
    return (
        "/Date("
        + str(int(datetime.fromisoformat(value).replace(tzinfo=TZ).timestamp() * 1000))
        + ")/"
    )


def row(party="Test Officer", **changes):
    item = {
        "Stock": "FPT",
        "TransactionMan": party,
        "TransactionManPosition": "Thành viên HĐQT",
        "RelatedManPosition": "",
        "VolumeBeforeTransaction": 1000,
        "PlanBuyVolume": 100,
        "PlanSellVolume": 0,
        "RealBuyVolume": 100,
        "RealSellVolume": 0,
        "VolumeAfterTransaction": 1100,
        "PlanBeginDate": ms("2026-09-20"),
        "PlanEndDate": ms("2026-10-05"),
        "RealEndDate": ms("2026-10-02"),
        "PublishedDate": ms("2026-09-10"),
        "OrderDate": ms("2026-09-20"),
        "TransactionNote": "Mua cp ESOP",
    }
    return item | changes


def normalized(rows):
    return v.normalize(rows, "FPT", "2026-10-30", NOW)


def bundle(rows, status="available"):
    records, issues, future = normalized(rows)
    assert not issues and not future
    return {
        "ticker": "FPT.VN",
        "asof": "2026-10-30",
        "status": status,
        "records": records,
        "source_page": "https://cafef.vn/test",
        "scope": v.SCOPE,
    }


class Session:
    def __init__(self, rows=None, error=False):
        self.headers = {}
        self.calls = []
        self.rows = rows if rows is not None else [row()]
        self.error = error

    def get(self, url, params, **kwargs):
        self.calls.append(params)
        if self.error:
            raise requests.ConnectionError("test offline")
        size = params["PageSize"]
        start = (params["PageIndex"] - 1) * size
        records = self.rows[start : start + size]
        payload = {"Data": {"TotalCount": len(self.rows), "Data": records}}

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return Response()


def test_registration_and_esop_not_misrepresented_as_market_execution():
    r = normalized([row()])[0][0]
    assert r["stage"] == "result_reported" and r["mechanism"] == "esop"
    assert r["source_announcement_date"] == "2026-09-10"
    assert r["reported_result_end"] == "2026-10-02"
    assert any("precedes completion" in n for n in r["quality_notes"])
    assert r["transaction_price"] is None
    plans = bundle([row(RealEndDate=None, RealBuyVolume=0, PlanEndDate=ms("2026-11-20"))])
    metrics = v.metrics(plans)
    assert metrics["vn_holder_dated_buy_shares_365d"]["value"] == 0
    assert metrics["vn_holder_planned_buy_shares_active"]["value"] == 100


def test_undated_result_is_visible_but_not_in_dated_subtotal():
    b = bundle([row(), row("Undated", RealEndDate=None, RealBuyVolume=1000)])
    assert v.summary(b)["undated_result_count"] == 1
    assert v.metrics(b)["vn_holder_dated_buy_shares_365d"]["value"] == 100
    assert "undated" in v.metrics(b)["vn_holder_dated_buy_shares_365d"]["formula"]


def test_future_result_quantities_not_counted_as_execution():
    b = bundle([row(RealEndDate=ms("2026-11-05"))])
    assert b["records"][0]["stage"] == "future_result_withheld"
    assert v.metrics(b)["vn_holder_dated_buy_shares_365d"]["value"] == 0


def test_missing_result_quantity_not_turned_into_zero():
    b = bundle([row(RealBuyVolume=None)])
    assert b["records"][0]["stage"] == "result_volume_missing"
    m = v.metrics(b)["vn_holder_dated_buy_shares_365d"]
    assert m["value"] is None and m["status"] == "missing"


def test_bad_old_registration_dates_retained_not_repaired_or_losing_result():
    b = bundle([row(PlanBeginDate=ms("2026-10-04"), PlanEndDate=ms("2026-10-01"))])
    r = b["records"][0]
    assert r["plan_begin"] == "2026-10-04" and r["plan_end"] == "2026-10-01"
    assert not r["plan_window_valid"] and r["quality_notes"]
    assert v.metrics(b)["vn_holder_dated_buy_shares_365d"]["value"] == 100


def test_bad_active_registration_not_sized_as_known_zero():
    b = bundle(
        [
            row(
                PlanBeginDate=ms("2026-11-10"),
                PlanEndDate=ms("2026-11-01"),
                RealEndDate=None,
                RealBuyVolume=0,
            )
        ]
    )
    assert v.metrics(b)["vn_holder_planned_buy_shares_active"]["value"] is None


def test_officers_related_and_unknown_actors_not_collapsed():
    assert v.actor("Thành viên HĐQT", "") == "insider"
    assert v.actor("Thành viên HĐQT", "Chủ tịch HĐQT") == "insider_and_related"
    assert v.actor("Con", "Chủ tịch HĐQT") == "related_party"
    assert v.actor("", "") == "other_or_unknown"
    assert v.actor("Cổ đông lớn", "") == "major_shareholder"
    b = bundle(
        [
            row(),
            row("Unknown", TransactionManPosition=""),
            row("Related", TransactionManPosition="Con", RelatedManPosition="Chủ tịch HĐQT"),
        ]
    )
    assert v.metrics(b)["vn_holder_dated_buy_shares_365d"]["value"] == 300
    assert v.metrics(b)["vn_insider_dated_buy_shares_365d"]["value"] == 100


def test_only_explicit_exchange_matched_mechanism_is_labelled_market():
    assert v.mechanism("") == "unspecified"
    assert v.mechanism("Bán thoả thuận") == "negotiated"
    assert v.mechanism("Mua cp ESOP") == "esop"
    assert v.mechanism("Quyền mua") == "rights_or_corporate_action"
    assert v.mechanism("Khớp lệnh trên sàn") == "explicit_exchange_matched"
    assert v.mechanism("Không khớp lệnh") == "unspecified"


def test_professional_projection_omits_identifiers_profiles_and_family_names():
    item = row(
        TransactionManPosition="Con",
        RelatedManPosition="Thành viên HĐQT",
        RelatedMan="Family name",
        IdentityNumber="sensitive",
        Address="private",
        Url="<script>bad</script>",
        HolderID="42",
    )
    p = v.project(item)
    assert p["TransactionManPosition"] == "Người liên quan"
    assert all(
        key not in p for key in ["IdentityNumber", "Address", "Url", "HolderID", "RelatedMan"]
    )


@pytest.mark.parametrize("bad", [True, -1, 1.5, float("inf")])
def test_invalid_share_quantities_not_silently_coerced(bad):
    records, issues, _ = normalized([row(RealBuyVolume=bad)])
    assert not records and issues


def test_future_source_announcement_and_wrong_ticker_not_used():
    records, issues, future = normalized([row(PublishedDate=ms("2026-11-01")), row(Stock="HPG")])
    assert not records and future == 1 and issues


def test_complete_pagination_and_all_records_returned_without_silent_truncation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(v.time, "sleep", lambda *_: None)
    monkeypatch.setattr(v, "PAGE_SIZE", 2)
    session = Session([row(str(n)) for n in range(5)])
    q = v.load("FPT.VN", "2026-10-30", tmp_path, now=NOW, session=session)
    assert len(session.calls) == 3 and q["source_total"] == len(q["records"]) == 5
    assert q["status"] == "available"
    csv = v.tool(q, "FPT.VN", "2026-10-30")
    assert all(str(n) + ",Thành viên HĐQT" in csv for n in range(5))
    assert all(c["StartDate"] == "" for c in session.calls)


def test_current_result_under_old_plan_date_cannot_leak_into_history(tmp_path, monkeypatch):
    monkeypatch.setattr(v.time, "sleep", lambda *_: None)
    v.load("FPT.VN", "2026-10-30", tmp_path, now=NOW, session=Session())
    session = Session()
    old = v.load("FPT.VN", "2026-09-20", tmp_path, now=NOW, session=session)
    assert not session.calls and old["status"] == "withheld"
    assert all(m["value"] is None for m in v.metrics(old).values())


def test_observed_vintage_can_be_replayed_without_new_download(tmp_path, monkeypatch):
    monkeypatch.setattr(v.time, "sleep", lambda *_: None)
    first = v.load("FPT.VN", "2026-10-29", tmp_path, now=NOW - timedelta(days=1), session=Session())
    session = Session(error=True)
    old = v.load("FPT.VN", "2026-10-29", tmp_path, now=NOW, session=session)
    assert old["records"] == first["records"] and not session.calls


def test_cache_refresh_error_retains_retrieved_time(tmp_path, monkeypatch):
    monkeypatch.setattr(v.time, "sleep", lambda *_: None)
    first = v.load("FPT.VN", "2026-10-30", tmp_path, now=NOW, session=Session())
    session = Session(error=True)
    v.load("FPT.VN", "2026-10-30", tmp_path, now=NOW + timedelta(minutes=10), session=session)
    assert not session.calls
    failed = v.load("FPT.VN", "2026-10-30", tmp_path, now=NOW + timedelta(hours=2), session=session)
    assert (
        failed["status"] == "cached_source_error"
        and failed["retrieved_at"] == first["retrieved_at"]
    )


def test_duplicate_boundary_or_page_cap_reports_partial_not_full_count(tmp_path, monkeypatch):
    monkeypatch.setattr(v.time, "sleep", lambda *_: None)
    monkeypatch.setattr(v, "PAGE_SIZE", 2)
    monkeypatch.setattr(v, "MAX_PAGES", 1)
    q = v.load(
        "FPT.VN", "2026-10-30", tmp_path, now=NOW, session=Session([row(str(n)) for n in range(3)])
    )
    assert q["status"] == "partial" and q["warnings"]
    assert all(m["value"] is None for m in v.metrics(q).values())


def test_original_insider_tool_routes_vn_and_restores_transport(tmp_path, monkeypatch):
    from desktop import vietnam
    from tradingagents.dataflows import router

    before = router.VENDOR_METHODS["get_insider_transactions"].get("vietnam")
    q = bundle([row()])
    with vietnam.vendor_scope(
        {"ticker": "FPT.VN", "analysis_date": "2026-10-30", "insider_dealings": q}
    ):
        fn = router.VENDOR_METHODS["get_insider_transactions"]["vietnam"]
        result = fn("FPT.VN", "2026-10-30")
        assert "Test Officer" in result and "esop" in result
        with pytest.raises(ValueError):
            fn("HPG.VN", "2026-10-30")
        with pytest.raises(ValueError):
            fn("FPT.VN", "2026-09-20")
    assert router.VENDOR_METHODS["get_insider_transactions"].get("vietnam") is before
