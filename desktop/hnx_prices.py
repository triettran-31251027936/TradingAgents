"""Published unadjusted daily OHLC for the public HNX listed-equity window."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

PAGE = "https://www.hnx.vn/vi-vn/co-phieu-etfs/du-lieu-thi-truong-ny.html"
TABLE = "https://www.hnx.vn/ModuleReportStockETFs/Report_MD_PriceVolatilyti/ListData_Listed"
_COLUMNS = {"open": "col_e", "high": "col_g", "low": "col_h", "close": "col_f"}
_DATE = "%d/%m/%Y"


def _html(url, fields=None):
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": PAGE} if fields else {}
    if sys.platform == "darwin":
        # Use native TLS trust on macOS. The default Python CA bundle may omit
        # intermediates available to the system HTTPS client.
        command = [
            "/usr/bin/curl", "--fail", "--silent", "--show-error", "--max-time", "8",
            "--max-filesize", "2097152", url,
        ]
        for key, value in headers.items():
            command.extend(("--header", f"{key}: {value}"))
        if fields:
            command.extend(("--data", urlencode(fields)))
        return subprocess.run(command, check=True, capture_output=True, timeout=10).stdout
    response = requests.request("POST" if fields else "GET", url, data=fields, headers=headers,
                                timeout=(4, 8))
    response.raise_for_status()
    return response.content


def observation(ticker, day):
    page = _html(PAGE)
    soup = BeautifulSoup(page, "html.parser")
    current = soup.select_one("#txtSymbol_Date")
    if not current or not current.get("value"):
        raise ValueError("HNX latest trading session could not be identified")
    latest = datetime.strptime(current["value"], _DATE).date()
    earliest_match = re.search(
        rb"CheckCompare2Date_ddMMyyyy\('(\d{2}/\d{2}/\d{4})',\s*_symbol_date\)", page
    )
    if not earliest_match:
        raise ValueError("HNX public window could not be identified")
    earliest = datetime.strptime(earliest_match[1].decode("ascii"), _DATE).date()
    if not earliest <= day <= latest:
        raise ValueError("HNX date is outside the public search window")
    fields = {
        "p_keysearch": f"{day.strftime(_DATE)}|0|0|'{ticker}'|0|{latest.strftime(_DATE)}",
        "pColOrder": "col_a", "pOrderType": "ASC", "pCurrentPage": "1",
        "pRecordOnPage": "50", "pIsSearch": "1",
    }
    body = _html(TABLE, fields)
    table = BeautifulSoup(body, "html.parser").select_one("#_tableDatas")
    if table is None:
        raise ValueError("HNX returned no quote table")
    columns = [th.get("id") for th in table.select("thead th")]
    row, = table.select("tbody tr")
    values = [td.get_text(strip=True) for td in row.select("td")]
    if len(columns) != len(values):
        raise ValueError("HNX quote table has an unexpected shape")
    isin = values[columns.index("col_k")]
    if values[columns.index("col_a")] != ticker or not re.fullmatch(r"VN[A-Z0-9]{10}", isin):
        raise ValueError("HNX returned a different instrument")
    prices = {}
    for key, column in _COLUMNS.items():
        value = values[columns.index(column)]
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)", value):
            raise ValueError("HNX price is not an integer VND amount")
        prices[key] = int(value.replace(".", ""))
    return {
        "date": day.isoformat(),
        "ticker": ticker,
        "isin": isin,
        "ohlc_vnd": prices,
        "publisher": "HNX",
        "url": TABLE,
        "sha256": hashlib.sha256(body).hexdigest(),
    }
