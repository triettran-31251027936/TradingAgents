"""Every published source period stays available through the original tools."""

import csv
import io

import pytest

from desktop import statements_vietnam as statements, vietnam
from tradingagents.dataflows import router
from tradingagents.dataflows.errors import NoMarketDataError


@pytest.fixture
def bundle():
    rows = [
        {
            "yearReport": y,
            "lengthReport": q,
            "publicDate": f"{y + 1}-03-30",
            "profit": y * 10 + q,
            "zero": 0,
            "missing": None,
        }
        for y in (2023, 2024, 2025)
        for q in (1, 2, 3, 4)
    ]
    annual = [
        {"yearReport": y, "lengthReport": 5, "publicDate": f"{y + 1}-03-30", "profit": y, "zero": 0}
        for y in range(2018, 2026)
    ]
    book = {
        "fields": {"profit": "Profit, after tax", "zero": "Zero line", "missing": "Null line"},
        "quarters": rows,
        "years": annual,
    }
    return {
        "ticker": "VHM.VN",
        "analysis_date": "2026-11-04",
        "statements": dict.fromkeys(("income", "balance", "cash"), book),
    }


def records(text):
    return list(csv.reader(io.StringIO(text[text.index("Period / publication date") :])))


@pytest.mark.parametrize("method", ["get_income_statement", "get_balance_sheet", "get_cashflow"])
def test_original_statement_tools_expose_every_eligible_quarter_and_year(bundle, method):
    with vietnam.vendor_scope(bundle):
        read = router.VENDOR_METHODS[method]["vietnam"]
        quarters = records(read("VHM.VN", "quarterly"))
        annual = records(read("VHM.VN", "Annual"))
    assert len(quarters) == 13 and len(annual) == 9
    assert quarters[1][0].startswith("2023-Q1 public:")
    assert quarters[-1][0].startswith("2025-Q4 public:")
    assert "Profit, after tax [profit]" in quarters[0]
    assert "Null line [missing]" not in quarters[0]
    assert all(row[2] == "0" for row in quarters[1:])
    assert annual[1][0].startswith("2018-FY public:")


def test_earlier_cutoff_filters_later_publications_and_keeps_missing_cells_empty(bundle):
    bundle["statements"]["income"]["quarters"][1]["profit"] = None
    rows = records(statements.read(bundle, "income", cutoff="2025-04-01"))
    assert len(rows) == 9 and all("2025-Q" not in row[0] for row in rows)
    assert rows[2][1] == "" and rows[2][2] == "0"
    with pytest.raises(NoMarketDataError):
        statements.read(bundle, "income", cutoff="2010-01-01")
    with pytest.raises(ValueError, match="after"):
        statements.read(bundle, "income", cutoff="2099-01-01")
    with pytest.raises(ValueError, match="frequency"):
        statements.read(bundle, "income", "monthly")


def test_empty_or_unlabelled_source_is_typed_no_data(bundle):
    bundle["statements"]["income"]["fields"] = {}
    with pytest.raises(NoMarketDataError):
        statements.read(bundle, "income")


def test_repeated_period_records_are_preserved_and_disclosed(bundle):
    record = {**bundle["statements"]["income"]["quarters"][0], "publicDate": "2026-01-01"}
    bundle["statements"]["income"]["quarters"].append(record)
    text = statements.read(bundle, "income")
    assert len(records(text)) == 14
    assert "do not sum duplicate periods" in text
