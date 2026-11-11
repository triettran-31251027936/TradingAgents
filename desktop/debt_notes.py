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


# Note 24.1, PDF pages 55 and 56. Each row preserves the disclosed range
# without assigning its balance to individual months within that range.
BANK_LOANS_MILLION = {
    "short_term": (
        ("VPBank", 12_176_786, "2026-10", "2027-07"),
        ("LPBank", 5_998_122, "2027-04", "2027-08"),
        ("Vietcombank", 4_990_988, "2026-10", "2027-06"),
        ("VIB", 5_061_155, "2026-10", "2027-07"),
        ("Techcombank", 5_715_734, "2026-11", "2027-07"),
        ("BIDV", 2_454_261, "2026-10", "2027-05"),
        ("VietinBank", 3_020_000, "2027-05", "2027-05"),
        ("HDBank", 2_813_630, "2026-10", "2027-06"),
        ("Bac A Bank", 2_241_130, "2027-01", "2027-04"),
        ("SHB", 1_880_952, "2026-10", "2027-02"),
        ("MB", 1_393_890, "2026-10", "2027-05"),
        ("Ngân hàng TMCP Thịnh vượng và Phát triển", 1_387_655, "2026-10", "2027-07"),
        ("Mizuho Bank", 1_296_750, "2026-11", "2026-11"),
        ("TPBank", 99_543, "2026-11", "2026-12"),
        ("SeABank", 499_621, "2027-05", "2027-05"),
    ),
    "current_portion_of_long_term": (
        ("Khoản vay hợp vốn 3", 1_280_000, "2027-08", "2027-08"),
        ("Vietcombank", 3_551_692, "2027-06", "2027-07"),
        ("MB", 356_250, "2027-05", "2027-05"),
        ("HDBank", 79_430, "2027-05", "2027-06"),
        ("VPBank", 474_131, "2027-06", "2027-06"),
    ),
    "long_term": (
        ("Khoản vay hợp vốn 3", 20_120_000, "2030-07", "2030-07"),
        ("Khoản vay hợp vốn 4", 19_500_000, "2030-09", "2030-09"),
        ("Vietcombank", 13_226_980, "2028-09", "2032-07"),
        ("MB", 1_268_750, "2028-02", "2030-11"),
        ("HDBank", 2_740_081, "2028-03", "2035-12"),
        ("TPBank", 3_299_774, "2027-12", "2027-12"),
        ("VPBank", 193_659, "2028-03", "2028-03"),
        ("VietinBank", 1_791_337, "2031-07", "2032-12"),
        ("BIDV", 2_866_500, "2031-07", "2031-07"),
    ),
}


def _bank_and_partner_details(balances, bonds_current, bonds_long_term):
    subtotals = {
        category: sum(row[1] for row in rows) for category, rows in BANK_LOANS_MILLION.items()
    }
    partner_current, partner_long, lease = 2_400_550, 1_000_000, 40_735
    if (
        subtotals != {"short_term": 51_030_217, "current_portion_of_long_term": 5_741_503,
                      "long_term": 65_007_081}
        or subtotals["short_term"] + subtotals["current_portion_of_long_term"]
        + partner_current + bonds_current != balances["short_term_borrowings"]
        or subtotals["long_term"] + partner_long + bonds_long_term + lease
        != balances["long_term_borrowings"]
    ):
        raise ValueError("VHM bank and partner borrowing notes do not reconcile")
    return {
        "bank_total_vnd": sum(subtotals.values()) * 1_000_000,
        "bank_subtotals_vnd": {k: v * 1_000_000 for k, v in subtotals.items()},
        "bank_rows": [
            {"category": category, "lender": lender, "carrying_vnd": amount * 1_000_000,
             "currency": "USD" if lender == "Mizuho Bank" else "VND",
             "principal_due_from": start, "principal_due_to": end,
             "pdf_page": 55 if category == "short_term" else 56}
            for category, rows in BANK_LOANS_MILLION.items()
            for lender, amount, start, end in rows
        ],
        "partner_current_carrying_vnd": partner_current * 1_000_000,
        "partner_current_narrative_vnd": 2_408_000_000_000,
        "partner_current_due_months": ["2026-09", "2027-01", "2027-03"],
        "partner_current_rate_pct": [8, 12],
        "partner_long_term_carrying_vnd": partner_long * 1_000_000,
        "partner_long_term_due_month": "2027-12",
        "partner_long_term_rate_pct": 9.5,
        "partner_note_pdf_page": 57,
        "finance_lease_carrying_vnd": lease * 1_000_000,
        "scope": (
            "Bank rows preserve reported principal maturity ranges or month. VND carrying values "
            "also apply to the USD row. A range does not allocate individual payments to months. "
            "Partner carrying amounts come from Note 24. Note 24.2 separately gives a different "
            "current narrative total with several months, without a breakdown by month. "
            "No later repayments, interest cash flows or undated allocations are inferred."
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
        "bank_and_partner": _bank_and_partner_details(balances, current, total - current),
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
