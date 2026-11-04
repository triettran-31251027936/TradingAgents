"""Expose all dated VN statement records without a silent four-period cap."""

import csv
import io
from datetime import date

from tradingagents.dataflows.errors import NoMarketDataError


def read(bundle, kind, freq="quarterly", cutoff=None):
    from desktop.vietnam import _published

    if not isinstance(freq, str) or freq.strip().lower() not in ("annual", "quarterly"):
        raise ValueError("Financial statement frequency must be annual or quarterly")
    freq = freq.strip().lower()
    cutoff = cutoff or bundle["analysis_date"]
    date.fromisoformat(cutoff)
    if cutoff > bundle["analysis_date"]:
        raise ValueError("Requested statement date is after the analysis snapshot")
    book = bundle["statements"][kind]
    period = "years" if freq == "annual" else "quarters"
    rows = sorted(
        (row for row in book[period] if _published(row, cutoff)),
        key=lambda row: (
            int(row["yearReport"]),
            int(row.get("lengthReport", 5)),
            str(row["publicDate"]),
        ),
    )
    fields = [
        (key, title)
        for key, title in book["fields"].items()
        if any(row.get(key) is not None for row in rows)
    ]
    if not rows or not fields:
        raise NoMarketDataError(
            bundle["ticker"],
            detail=f"No labelled {freq} {kind} statement values published by {cutoff}",
        )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Period / publication date", *[f"{title} [{key}]" for key, title in fields]])
    for row in rows:
        label = f"{row['yearReport']}{'-FY' if freq == 'annual' else '-Q' + str(row.get('lengthReport'))} public:{str(row['publicDate'])[:10]}"
        writer.writerow([label, *["" if row.get(key) is None else row[key] for key, _ in fields]])
    return (
        f"Vietcap {kind} financial statements published by {cutoff}; raw VND except per-share/ratio/share-count fields.\n"
        f"Retained provider records: {len(rows)} ({freq}); all eligible records in this snapshot, not an exhaustive filing archive.\n"
        "One source period/record per row. Repeated periods may be revisions; do not sum duplicate periods. "
        "Periods as reported; do not assume CFO quarter vs YTD. Empty cell is missing, not zero. "
        "Restated data are not a guaranteed point-in-time vintage.\n" + output.getvalue()
    )
