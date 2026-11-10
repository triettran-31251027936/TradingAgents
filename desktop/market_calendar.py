"""Published common market closures used by the Vietnam paper account."""

HOLIDAYS = {
    2025: frozenset({
        (1, 1), (1, 27), (1, 28), (1, 29), (1, 30), (1, 31),
        (4, 7), (4, 30), (5, 1), (5, 2), (9, 1), (9, 2),
    }),
    2026: frozenset({
        (1, 1), (1, 2), (2, 16), (2, 17), (2, 18), (2, 19), (2, 20),
        (4, 27), (4, 30), (5, 1), (8, 31), (9, 1), (9, 2),
    }),
}
SOURCES = {
    2025: ("https://vsdc.vn/vi/ad/178644",),
    2026: (
        "https://www.hsx.vn/vi/tin-tuc/hose-thong-bao-lich-nghi-giao-dich-nam-2026/2437411",
        "https://www.hnx.vn/vi-vn/chi-tiet-lich-nghi-gd-60021971.html",
        "https://hnx.vn/vi-vn/ket-qua-tim-kiem/chi-tiet-tin-591376-0.html",
        "https://vsdc.vn/vi/ad1/190545",
    ),
}


def trading_day(day):
    return day.weekday() < 5 and (day.month, day.day) not in HOLIDAYS.get(day.year, ())
