"""Finish a committed report/proposal after a crash, without constructing models.

The receipt commits in the same SQLite transaction as the proposal. Failed
exports therefore cannot leave a recoverable receipt or orphan proposal.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from desktop import vietnam

STATE_FIELDS = (
    "company_of_interest",
    "trade_date",
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_debate_state",
    "trader_investment_plan",
    "risk_debate_state",
    "investment_plan",
    "final_trade_decision",
    "final_rating",
    "past_context",
    "memory_note",
)


class RequestBusy(ValueError):
    """Another process still owns this request; it is not a crashed job."""


def identity(cfg, ticker, day, chosen, asset, portfolio, _options):
    return vietnam.digest(
        {
            "version": 1,
            "config": cfg,
            "ticker": ticker,
            "date": day,
            "analysts": chosen,
            "asset": asset,
            "portfolio": portfolio.model_dump(mode="json") if portfolio is not None else None,
        }
    )


@contextmanager
def request_lock(cfg, key):
    from tradingagents.dataflows.files import locked

    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Mã biên nhận phục hồi không hợp lệ.")
    folder = Path(cfg["data_cache_dir"]) / "publication-locks"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / key
    if path.with_name(path.name + ".lock").is_symlink():
        raise ValueError("Không khóa lượt phân tích qua file liên kết.")
    with locked(path, wait=False) as acquired:
        if not acquired:
            raise RequestBusy(
                "Cùng yêu cầu đang được xử lý ở tiến trình khác; không chạy model lần hai."
            )
        yield


def eligible_store(cfg, ticker, portfolio, options):
    vn = cfg.get("desktop_vietnam", {})
    if not (
        vn.get("enabled")
        and vn.get("paper_enabled")
        and vietnam.is_vietnam(ticker)
        and portfolio is None
    ):
        return None
    from desktop.practice import PracticeStore

    return PracticeStore(cfg["data_cache_dir"])


def receipt(folder, cfg, ticker, day, signature, settings, state, result, runtime):
    files = {}
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise ValueError("Không lưu biên nhận báo cáo qua file liên kết.")
        if path.is_file() and path.name != ".publishing":
            files[str(path.relative_to(folder))] = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = runtime._checkpoint.path.name if runtime and runtime._checkpoint else None
    return {
        "version": 1,
        "ticker": ticker,
        "date": day,
        "run_signature": signature,
        "checkpoint_enabled": bool(cfg.get("checkpoint_enabled")),
        "runtime_metadata": meta,
        "settings": settings,
        "state": {key: copy.deepcopy(state[key]) for key in STATE_FIELDS if key in state},
        "result": copy.deepcopy(result),
        "files": files,
    }


def verify(value, cfg, ticker, day):
    if value["version"] != 1 or value["ticker"] != ticker or value["date"] != day:
        raise ValueError("Publication identity mismatch")
    root = Path(cfg["results_dir"]).resolve()
    report = root / value["result"]["report_id"]
    if (
        report.name != "complete_report.md"
        or not report.resolve().is_relative_to(root)
        or not report.is_file()
        or report.is_symlink()
    ):
        raise ValueError("Invalid report path")
    folder = report.parent
    for name, digest in value["files"].items():
        path = folder / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or not path.resolve().is_relative_to(folder.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Publication artifact mismatch")
    if "complete_report.md" not in value["files"]:
        raise ValueError("Report manifest missing")
    state = value["state"]
    if state["company_of_interest"] != ticker or state["trade_date"] != day:
        raise ValueError("State identity mismatch")
    decision = folder / "5_portfolio/decision.md"
    if decision.read_text() != state["final_trade_decision"]:
        raise ValueError("Decision mismatch")
    meta = value["runtime_metadata"]
    if meta is not None and not re.fullmatch(r"[0-9a-f]{64}\.json", meta):
        raise ValueError("Invalid runtime checkpoint name")
    return folder


def finish(store, key, value, cfg):
    from tradingagents.graph.checkpointer import checkpoint_step

    if (
        value["checkpoint_enabled"]
        and checkpoint_step(
            cfg["data_cache_dir"], value["ticker"], value["date"], value["run_signature"]
        )
        is not None
    ):
        raise ValueError(
            "Checkpoint chưa được dọn; giữ biên nhận để phục hồi, không chạy model lại."
        )
    store.finish_publication(key)


def recover(store, key, cfg, ticker, day, graph_type):
    value = store.pending_publication(key) if store else None
    if value is None:
        return None
    try:
        folder = verify(value, cfg, ticker, day)
        from tradingagents.graph.checkpointer import clear_checkpoint
        from tradingagents.memory import TradingMemoryLog

        # Native recording methods need only config, settings and the memory
        # writer. Bypass __init__: it builds clients and the graph unnecessarily.
        recorder = object.__new__(graph_type)
        recorder.config = copy.deepcopy(cfg)
        recorder.memory_log = TradingMemoryLog(cfg)
        recorder.run_settings = lambda: copy.deepcopy(value["settings"])
        recorder.record_decision(ticker, day, value["state"])
        if value["checkpoint_enabled"]:
            clear_checkpoint(cfg["data_cache_dir"], ticker, day, value["run_signature"])
        if value["runtime_metadata"]:
            (
                Path(cfg["data_cache_dir"]) / "desktop-checkpoints" / value["runtime_metadata"]
            ).unlink(missing_ok=True)
        (folder / ".publishing").unlink(missing_ok=True)
        finish(store, key, value, cfg)
        return {**value["result"], "recovered_publication": True}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise ValueError(
            "Có báo cáo/đề xuất đã lưu nhưng phục hồi chưa hoàn tất hoặc file đã thay đổi. "
            "Giữ biên nhận để kiểm tra; không gọi model hay tạo lệnh mới."
        ) from exc


def resume_pending(cfg, graph_type):
    """Finalize committed jobs at startup; skip requests still owned by a live process."""
    if not cfg.get("desktop_vietnam", {}).get("paper_enabled"):
        return {"finished": [], "busy": [], "failed": []}
    from desktop.practice import PracticeStore

    results = {"finished": [], "busy": [], "failed": []}
    try:
        store = PracticeStore(cfg["data_cache_dir"])
        pending = store.pending_publications()
    except (OSError, ValueError, sqlite3.Error):
        results["failed"].append("account_storage")
        return results
    for key in pending:
        try:
            with request_lock(cfg, key):
                value = store.pending_publication(key)
                if value is None:
                    continue
                recover(store, key, cfg, value["ticker"], value["date"], graph_type)
                results["finished"].append(key)
        except RequestBusy:
            results["busy"].append(key)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            results["failed"].append(key)
    return results
