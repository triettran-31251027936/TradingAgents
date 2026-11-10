"""Issuer correction must change the dated evidence only after publication."""

from datetime import datetime

from desktop import insider_vietnam, ptsc_dealings as ptsc


def article(day, title, body):
    return (
        f'<div class="heading"><h1>{title}</h1><div class="sub-title"><time>{day}</time></div></div>'
        f'<div class="boding">{body}</div>'
    ).encode()


def test_corrected_sale_uses_publication_cutoff_and_not_par_value():
    sale = (
        "- Tên cá nhân thực hiện giao dịch: Nguyễn Văn A - Chức vụ hiện nay tại PTSC: Tổng Giám đốc "
        "- Mã chứng khoán: PVS - Số lượng cổ phiếu đăng ký bán: 1.200 "
        "- Số lượng cổ phiếu nắm giữ trước khi thực hiện giao dịch: 1.200 "
        "- Số lượng cổ phiếu đã bán: 1.000 - Số lượng cổ phiếu nắm giữ sau khi thực hiện giao dịch: 200 "
        "- Ngày bắt đầu giao dịch: 1/12/2025 - Ngày kết thúc giao dịch: 19/12/2025"
    )
    correction = (
        "Đính chính báo cáo kết quả giao dịch cổ phiếu ngày 22/12/2025 của Ông Nguyễn Văn A – Tổng Giám đốc "
        "- Loại giao dịch đã thực hiện: bán - Số lượng cổ phiếu đã giao dịch: 1.100 "
        "- Giá trị tính theo mệnh giá: 11.000.000 "
        "Số lượng cổ phiếu PVS tôi sở hữu sau ngày 19/12/2025 là 200 CP, gồm 100 CP được phân bổ thêm."
    )
    original_url = ptsc.PAGE + "/ket-qua"
    correction_url = ptsc.PAGE + "/dinh-chinh"
    index = "".join(
        f'<div class="news-card-item"><time>{day}</time><h2 class="title"><a href="{url}">{title}</a></h2></div>'
        for day, url, title in (
            ("14.01.2026", correction_url, "Đính chính"),
            ("24.12.2025", original_url, "Kết quả"),
        )
    ).encode()

    class Response:
        def __init__(self, url, content):
            self.url = url
            self.content = content

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self, correction_body=correction):
            self.calls = []
            self.documents = {
                ptsc.PAGE: index,
                original_url: article("24/12/2025", "Kết quả", sale),
                correction_url: article("14/01/2026", "Đính chính Kết quả", correction_body),
            }

        def get(self, url, **kwargs):
            self.calls.append(url)
            return Response(url, self.documents[url])

    now = datetime(2026, 11, 10, 18, tzinfo=ptsc.TZ)
    session = Session()
    earlier = ptsc.load("2026-01-13", now=now, session=session)
    assert session.calls == [ptsc.PAGE, original_url]
    assert earlier["records"][0]["reported_sell"] == 1000
    updated = ptsc.load("2026-01-14", now=now, session=Session())
    assert updated["records"][0]["reported_sell"] == 1100
    assert updated["records"][0]["original_reported_sell"] == 1000
    assert updated["records"][0]["result_published"] == "2026-01-14"
    values = insider_vietnam.metrics(updated)
    assert values["vn_insider_dated_sell_shares_365d"]["value"] == 1100
    assert values["vn_holder_records_announced_365d"]["value"] is None
    assert "11.000.000" not in insider_vietnam.tool(updated, "PVS.VN", "2026-01-14")
    unresolved = ptsc.load("2026-01-14", now=now, session=Session(correction.replace("1.100", "1.101")))
    assert unresolved["status"] == "withheld"
    assert not unresolved["records"]
