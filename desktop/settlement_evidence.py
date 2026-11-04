"""Receipts for the exact VN closes consumed by the original outcome evaluator."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from desktop import vietnam

BASIS = (
    "Vietcap daily close as returned by the source at retrieval time. Corporate-action "
    "adjustment factors and cash-dividend reinvestment are not independently verified. "
    "This is an observed close-series return, not certified total return or account P&L. "
    "Historical rows can be revised by the source; this receipt freezes the values used."
)


def calculation(stock, bench, days, outcome):
    """Explain selected endpoints only after the unchanged evaluator resolved them."""
    if outcome[0] is None:
        return None
    entry, exit_ = stock.index[0], stock.index[days]
    b0, b1 = float(bench.asof(entry)), float(bench.asof(exit_))
    return {
        "entry_date": entry.strftime("%Y-%m-%d"),
        "exit_date": exit_.strftime("%Y-%m-%d"),
        "stock_entry_close": float(stock.iloc[0]),
        "stock_exit_close": float(stock.iloc[days]),
        "benchmark_entry_date": bench.loc[:entry].index[-1].strftime("%Y-%m-%d"),
        "benchmark_exit_date": bench.loc[:exit_].index[-1].strftime("%Y-%m-%d"),
        "benchmark_entry_close": b0,
        "benchmark_exit_close": b1,
        "benchmark_return": (b1 - b0) / b0,
        "formula": "stock_exit / stock_entry - 1; alpha = stock_return - benchmark_return",
    }


@contextmanager
def capture(cfg, is_vietnam):
    from tradingagents.memory import settlement

    original_fetch, original_closes = settlement.fetch_returns, settlement.get_closes
    receipts, active = [], []

    def closes(ticker, start, end):
        values = original_closes(ticker, start, end)
        if active:
            clean = settlement._by_day(values)
            active[-1]["calls"].append(
                {
                    "ticker": ticker,
                    "start": start,
                    "end_exclusive": end,
                    "timezone": str(getattr(values.index, "tz", None)),
                    "source_snapshot_fingerprint": values.attrs.get("vn_snapshot_fingerprint"),
                    "source_receipts": values.attrs.get("vn_source_receipts", []),
                    "source": vietnam.CHART
                    if is_vietnam(ticker)
                    else "original upstream get_closes",
                    "closed_rows": [
                        {"date": k.strftime("%Y-%m-%d"), "close": float(v)}
                        for k, v in clean.items()
                    ],
                }
            )
            active[-1]["series"].append((ticker, clean))
        return values

    def fetch(ticker, trade_date, holding_days=5, benchmark="SPY"):
        if not is_vietnam(ticker):
            return original_fetch(ticker, trade_date, holding_days, benchmark=benchmark)
        trace = {"calls": [], "series": []}
        active.append(trace)
        receipt = {
            "version": 1,
            "ticker": ticker,
            "analysis_date": trade_date,
            "holding_days": holding_days,
            "benchmark": benchmark,
            "observed_at": datetime.now(UTC).isoformat(),
            "price_basis": BASIS,
            "scope": "Outcome-price receipt; does not certify the model decision or reflection completed.",
        }
        try:
            result = original_fetch(ticker, trade_date, holding_days, benchmark=benchmark)
            receipt.update(
                status="resolved" if result[0] is not None else "pending",
                outcome={
                    "raw_return": result[0],
                    "alpha_return": result[1],
                    "holding_days": result[2],
                    "resolution_date": result[3],
                },
            )
            if result[0] is not None:
                # The evaluator may extend a halted/holiday window. Its final
                # two reads are the instrument and benchmark actually scored.
                stock = trace["series"][-2][1]
                stock = stock[stock.index > trade_date]
                bench = trace["series"][-1][1]
                receipt["calculation"] = calculation(stock, bench, holding_days, result)
            return result
        except Exception as exc:
            receipt.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            active.pop()
            receipt["price_reads"] = trace["calls"]
            receipt["content_sha256"] = vietnam.digest(receipt)
            path = (
                Path(cfg["data_cache_dir"]) / "settlement-evidence" / (uuid.uuid4().hex + ".json")
            )
            # A failed write leaves the original decision pending: do not claim
            # audited settlement without its actual price evidence.
            vietnam.write_json(path, receipt)
            receipts.append(
                {
                    "file": path.name,
                    "sha256": receipt["content_sha256"],
                    "ticker": ticker,
                    "analysis_date": trade_date,
                    "status": receipt["status"],
                    "price_basis": BASIS,
                }
            )

    settlement.fetch_returns, settlement.get_closes = fetch, closes
    try:
        yield receipts
    finally:
        settlement.fetch_returns, settlement.get_closes = original_fetch, original_closes
