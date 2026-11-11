"""Verified issuer borrowing notes tied to a dated consolidated balance sheet."""

from collections import defaultdict

VHM_Q3_2026_URL = (
    "https://staticfile.hsx.vn/Uploads/UploadDocuments/2510460/"
    "20261030%20-%20VHM%20-%20Bao%20cao%20tai%20chinh%20hop%20nhat%20"
    "Q3%20nam%202026%20va%20giai%20trinh%20chenh%20lech.pdf"
)
VHM_Q3_2026_SHA256 = "5257495758beca2c03fd9112220d76714819d26b0289aa66e8d8a5f6bb41b8d7"
# HNX published this filing for VHM121025 on 3 November. Use that verified
# disclosure cutoff for backtests even though the document is dated 30 October.
VHM_Q3_2026_AVAILABLE = "2026-11-03"
VHM_Q3_2026_DISCLOSURE = (
    "https://hnx.vn/vi-vn/m-niem-yet/tin-tuc/Bao%20cao%20tai%20chinh%20quy%2032026-640863-1.html"
)
# Note 24.3, PDF pages 58 and 59 (printed pages 53 and 54).
# Amounts are in VND million, as printed in the issuer's consolidated filing.
BONDS_MILLION = {
    58: (
        ("2028-06", 14_256_008),
        ("2029-02", 7_919_747),
        ("2028-11", 6_932_692),
        ("2027-12", 6_459_271),
        ("2028-10", 5_926_597),
        ("2028-12", 4_449_309),
        ("2027-12", 3_974_896),
        ("2027-10", 3_882_016),
        ("2027-11", 3_873_146),
        ("2027-03", 2_992_545),
        ("2029-05", 2_959_940),
        ("2029-06", 2_385_150),
        ("2026-11", 2_088_920),
        ("2027-11", 1_986_677),
        ("2027-10", 1_986_106),
        ("2029-05", 1_974_056),
        ("2029-05", 1_973_806),
        ("2029-05", 1_973_811),
        ("2029-05", 1_973_680),
        ("2028-05", 1_976_667),
        ("2029-06", 1_970_000),
        ("2029-06", 1_972_670),
        ("2029-08", 1_972_430),
    ),
    59: (
        ("2027-12", 1_931_841),
        ("2029-06", 2_843_408),
        ("2027-03", 997_471),
        ("2029-05", 986_720),
        ("2029-05", 986_632),
        ("2029-06", 986_267),
        ("2029-08", 986_192),
        ("2029-06", 2_362_262),
        ("2029-07", 1_972_166),
        ("2029-07", 986_037),
        ("2029-08", 1_971_437),
        ("2028-08", 993_499),
        ("2028-09", 1_991_338),
        ("2028-09", 7_546_770),
    ),
}


def load(ticker, asof, metrics):
    if ticker != "VHM" or asof < VHM_Q3_2026_AVAILABLE:
        return {}
    balances = {
        "short_term_borrowings": 65_251_206,
        "long_term_borrowings": 175_371_060,
        "gross_borrowings": 240_622_266,
    }
    for name, amount in balances.items():
        metric = metrics.get(name, {})
        if not (
            metric.get("asof", "").startswith("2026-Q3 public:")
            and metric.get("value") == amount * 1_000_000
        ):
            return {}
    monthly = defaultdict(int)
    pages = defaultdict(set)
    for page, rows in BONDS_MILLION.items():
        for month, amount in rows:
            monthly[month] += amount
            pages[month].add(page)
    total = sum(monthly.values())
    current = sum(amount for month, amount in monthly.items() if month <= "2027-09")
    if (total, current, total - current) != (115_402_180, 6_078_936, 109_323_244):
        raise ValueError("VHM bond disclosure rows do not reconcile")
    return {
        "status": "available",
        "statement_date": "2026-09-30",
        "available_by": VHM_Q3_2026_AVAILABLE,
        "disclosure_url": VHM_Q3_2026_DISCLOSURE,
        "source": {
            "name": "Thuyết minh nợ vay hợp nhất VHM · HOSE",
            "url": VHM_Q3_2026_URL,
            "sha256": VHM_Q3_2026_SHA256,
            "published": VHM_Q3_2026_AVAILABLE,
            "status": "available",
        },
        "borrowings_vnd": {name: amount * 1_000_000 for name, amount in balances.items()},
        "bond_carrying_vnd": total * 1_000_000,
        "bond_current_carrying_vnd": current * 1_000_000,
        "bond_long_term_carrying_vnd": (total - current) * 1_000_000,
        "reported_bond_rows": sum(map(len, BONDS_MILLION.values())),
        "maturities": [
            {"month": month, "carrying_vnd": amount * 1_000_000, "pdf_pages": sorted(pages[month])}
            for month, amount in sorted(monthly.items())
        ],
        "scope": (
            "Consolidated Note 24 and 24.3. These are carrying amounts at 2026-09-30 "
            "grouped by the disclosed final maturity month. Use the statement date for "
            "amounts. The table excludes later changes, future interest and exact dates "
            "or instalments of bank/partner loans. It is not a principal cash-flow forecast."
        ),
    }
