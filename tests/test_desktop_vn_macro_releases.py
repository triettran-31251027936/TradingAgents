"""Period-specific release facts must not turn nine-month averages into quarters."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from desktop import macro_releases as m

NOW = datetime(2026, 10, 30, 12, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))


def post(kind="cpi"):
    title = (
        "Chỉ số giá tiêu dùng tháng Chín, quý III và 9 tháng năm 2026"
        if kind == "cpi"
        else "Thông cáo báo chí về tình hình lao động, việc làm quý III và 9 tháng năm 2026"
    )
    body = (
        "Chỉ số giá tiêu dùng (CPI) tháng Chín tăng 0,62% so với tháng trước; tăng 4,20% so với tháng 12/2025 và tăng 5,08% so với cùng kỳ năm trước. Bình quân chín tháng năm 2026, CPI tăng 4,52% so với cùng kỳ năm trước. Chỉ số giá vàng tăng 12,42% so với cùng kỳ năm trước."
        if kind == "cpi"
        else "Tỷ lệ thiếu việc làm trong độ tuổi lao động là 1,53%. Trong quý III/2026, tỷ lệ thất nghiệp trong độ tuổi lao động là 2,22%. Tỷ lệ thiếu việc làm trong độ tuổi lao động quý III/2026 là 1,31%. Tỷ lệ thất nghiệp của thanh niên (từ 15-24 tuổi) quý III/2026 là 9,07%. Tính chung chín tháng năm 2026, tỷ lệ thất nghiệp của thanh niên là 8,87%."
    )
    return {
        "id": 1,
        "date_gmt": "2026-10-03T01:23:23",
        "modified_gmt": "2026-10-12T01:23:23",
        "link": "https://www.nso.gov.vn/release/",
        "title": {"rendered": title},
        "content": {"rendered": "<p>" + body + "</p>"},
    }


def facts(kind):
    return m.facts(m.normalize([post(kind)], kind, "2026-10-30", NOW)[0], kind)


def test_cpi_month_yoy_does_not_use_ytd_gold_or_average():
    r = facts("cpi")
    assert r["vn_release_cpi_yoy"]["value"] == 0.0508
    assert r["vn_release_cpi_mom"]["value"] == 0.0062
    assert r["vn_release_cpi_yoy"]["asof"] == "2026-09"
    assert r["vn_release_cpi_yoy"]["published"].startswith("2026-10-03")


def test_quarterly_underemployment_not_nine_month_summary():
    r = facts("labor")
    assert r["vn_release_underemployment"]["value"] == 0.0131
    assert r["vn_release_youth_unemployment"]["value"] == 0.0907
    assert r["vn_release_unemployment"]["value"] == 0.0222
    assert all(v["asof"] == "2026-Q3" for v in r.values())


def test_negative_cpi_change_sign_preserved():
    p = post()
    p["content"]["rendered"] = p["content"]["rendered"].replace("tăng 0,62", "giảm 0,62")
    r = m.facts(m.normalize([p], "cpi", "2026-10-30", NOW)[0], "cpi")
    assert r["vn_release_cpi_mom"]["value"] == -0.0062


def test_conflicting_same_period_values_not_selected_arbitrarily():
    p = post("labor")
    p["content"]["rendered"] += (
        " Trong quý III/2026, tỷ lệ thất nghiệp trong độ tuổi lao động là 2,23%."
    )
    r = m.facts(m.normalize([p], "labor", "2026-10-30", NOW)[0], "labor")
    assert "vn_release_unemployment" not in r


@pytest.mark.parametrize(
    "field,value",
    [
        ("date_gmt", "2026-11-01T00:00:00"),
        ("modified_gmt", "2026-11-01T00:00:00"),
        ("date_gmt", ""),
        ("link", "https://untrusted.invalid/release"),
    ],
)
def test_future_revision_invalid_date_or_untrusted_link_withheld(field, value):
    p = post()
    p[field] = value
    assert m.normalize([p], "cpi", "2026-10-30", NOW) == []


def test_historical_query_excludes_revised_after_date():
    assert m.normalize([post()], "cpi", "2026-10-05", NOW) == []


def test_irrelevant_title_or_changed_prose_does_not_produce_fake_number():
    p = post()
    p["title"]["rendered"] = "Cục Thống kê tổ chức tập huấn"
    assert m.normalize([p], "cpi", "2026-10-30", NOW) == []
    p = post()
    p["content"]["rendered"] = "No matching quantitative statement."
    assert m.facts(m.normalize([p], "cpi", "2026-10-30", NOW)[0], "cpi") == {}


def test_gdp_quarterly_growth_not_nine_month_average_or_target():
    p = post("labor")
    p["title"]["rendered"] = (
        "Thông cáo báo chí tình hình kinh tế - xã hội quý III và 9 tháng năm 2026"
    )
    p["content"]["rendered"] = (
        "Tổng sản phẩm trong nước (GDP) quý III/2026 ước tăng 9,95% so với cùng kỳ năm trước. GDP chín tháng tăng 9,01%; mục tiêu cả năm tăng 10%."
    )
    rows = m.normalize([p], "gdp", "2026-10-30", NOW)
    result = m.facts(rows[0], "gdp")
    assert result["vn_release_gdp_yoy"]["value"] == pytest.approx(0.0995)
    assert result["vn_release_gdp_yoy"]["asof"] == "2026-Q3"


def test_labor_counts_keep_quarter_scope_units_and_rounded_estimate():
    p = post("labor")
    p["content"]["rendered"] += (
        " Lực lượng lao động từ 15 tuổi trở lên của cả nước quý III/2026 ước đạt 54,1 triệu người."
        " Tính chung chín tháng, lực lượng lao động là 53,8 triệu người."
        " Lao động có việc làm quý III/2026 đạt gần 53,0 triệu người."
        " Lao động có việc làm chín tháng đạt 52,7 triệu người."
    )
    r = m.facts(m.normalize([p], "labor", "2026-10-30", NOW)[0], "labor")
    for alias, value in [("vn_release_labor_force", 54_100_000),
                         ("vn_release_employment", 53_000_000)]:
        assert r[alias]["value"] == value
        assert r[alias]["unit"] == "persons"
        assert r[alias]["asof"] == "2026-Q3"
        assert r[alias]["precision"] == "rounded_estimate"
        assert "1,000,000" in r[alias]["formula"]
        assert r[alias]["source_quote"] and r[alias]["source_content_sha256"]


def test_labor_count_conflict_and_other_periods_are_not_guessed():
    p = post("labor")
    p["content"]["rendered"] += (
        " Lực lượng lao động từ 15 tuổi trở lên của cả nước quý III/2026 ước là 54,1 triệu người."
        " Lực lượng lao động từ 15 tuổi trở lên của cả nước quý III/2026 ước là 54,2 triệu người."
        " Lao động có việc làm quý II/2026 đạt 52,0 triệu người."
        " Lao động có việc làm quý III/2026 trong nông nghiệp là 13,3 triệu người."
    )
    r = m.facts(m.normalize([p], "labor", "2026-10-30", NOW)[0], "labor")
    assert "vn_release_labor_force" not in r
    assert "vn_release_employment" not in r


def test_labor_count_future_revision_stays_withheld():
    p = post("labor")
    p["content"]["rendered"] = (
        "Lao động có việc làm quý III/2026 đạt gần 53,0 triệu người."
    )
    p["modified_gmt"] = "2026-11-01T00:00:00"
    assert not m.normalize([p], "labor", "2026-10-30", NOW)
