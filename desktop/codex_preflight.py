"""Read-only CLI quota receipt before uncached desktop inference.

Protocol: https://learn.chatgpt.com/docs/app-server . No thread, turn, login,
reset redemption or credit purchase is ever requested by this module.
"""

from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

SCOPE = "Codex CLI account snapshot, not a reservation, model-entitlement test, USD estimate or guarantee of capacity for a full run."


def unknown(reason):
    return {"status": "unavailable", "reason": reason, "windows": {}, "scope": SCOPE}


def summarize(result, *, now=None):
    if not isinstance(result, dict):
        return unknown("invalid_response")
    buckets = result.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    if bucket is None:
        bucket = result.get("rateLimits")
    if not isinstance(bucket, dict) or bucket.get("limitId") not in (None, "codex"):
        return unknown("codex_bucket_unavailable")
    stamp = time.time() if now is None else now
    windows = {}
    for name in ("primary", "secondary"):
        row = bucket.get(name)
        if not isinstance(row, dict):
            continue
        used, duration, reset = (
            row.get(k) for k in ("usedPercent", "windowDurationMins", "resetsAt")
        )
        if (
            type(used) not in (int, float)
            or not math.isfinite(used)
            or not 0 <= used <= 100
            or type(duration) is not int
            or duration <= 0
            or type(reset) is not int
            or reset <= stamp
        ):
            continue
        windows[name] = {
            "used_percent": used,
            "remaining_percent": 100 - used,
            "window_minutes": duration,
            "resets_at": reset,
        }
    credits = bucket.get("credits") or result.get("credits") or {}
    credit_capacity = False
    if isinstance(credits, dict):
        try:
            balance = Decimal(str(credits.get("balance", "0")))
            credit_capacity = credits.get("unlimited") is True or (
                credits.get("hasCredits") is True and balance.is_finite() and balance > 0
            )
        except InvalidOperation:
            pass
    allowed = result.get("ordinaryUsageAllowed")
    spend_block = result.get("spendControlReached") is True
    reached = bucket.get("rateLimitReachedType")
    if spend_block or allowed is False:
        status = "exhausted"
    elif allowed is True:
        status = "available"
    elif reached or (
        any(w["used_percent"] >= 100 for w in windows.values()) and not credit_capacity
    ):
        status = "exhausted" if not credit_capacity else "available"
    else:
        status = "available" if windows else "unavailable"
    return {
        "status": status,
        "windows": windows,
        "credit_capacity_reported": credit_capacity,
        "observed_at": datetime.now(UTC).isoformat(),
        "scope": SCOPE,
    }


def rpc_snapshot(executable, *, timeout=6):
    """Bounded stdio RPC; never retain stderr/raw account data in receipts."""
    process = None
    try:
        process = subprocess.Popen(
            [executable, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )

        def send(value):
            process.stdin.write((json.dumps(value) + "\n").encode())
            process.stdin.flush()

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "tradingagents_quota_check",
                        "title": "Trading Agents",
                        "version": "1",
                    }
                },
            }
        )
        deadline, total, buffer = time.monotonic() + timeout, 0, b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    return unknown("timeout")
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    return unknown("closed_before_response")
                total += len(data)
                buffer += data
                if total > 1048576:
                    return unknown("response_too_large")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        message = json.loads(line)
                    except ValueError:
                        return unknown("invalid_protocol")
                    if not isinstance(message, dict):
                        return unknown("invalid_protocol")
                    if message.get("id") == 1:
                        if "error" in message or not isinstance(message.get("result"), dict):
                            return unknown("initialization_failed")
                        send({"method": "initialized"})
                        send({"id": 2, "method": "account/rateLimits/read"})
                    elif message.get("id") == 2:
                        return (
                            unknown("quota_read_failed")
                            if "error" in message
                            else summarize(message.get("result"))
                        )
                    elif "id" in message and "method" in message:
                        send(
                            {
                                "id": message["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Read-only quota check does not support this request",
                                },
                            }
                        )
        return unknown("timeout")
    except (OSError, ValueError, TypeError, OverflowError):
        return unknown("cli_unavailable")
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()


def read_limits():
    executable = shutil.which(os.environ.get("TRADINGAGENTS_CODEX_BIN") or "codex")
    return rpc_snapshot(executable) if executable else unknown("cli_not_found")


class Preflight:
    def __init__(self, cfg, emit):
        from desktop.model_routing import ROLE_NAMES

        routes = cfg.get("desktop_role_models", {})
        self.required = any(
            str(
                routes.get(role, {}).get("provider")
                or cfg.get(
                    ("deep" if role in ("Research Manager", "Portfolio Manager") else "quick")
                    + "_think_provider"
                )
                or cfg.get("llm_provider")
                or ""
            ).lower()
            == "codex_cli"
            for role in ROLE_NAMES
        )
        self.emit, self.lock, self.receipt = emit, threading.Lock(), None

    def check(self):
        if not self.required:
            return
        with self.lock:
            if self.receipt is None:
                self.receipt = read_limits()
                self.emit("provider_preflight", {"provider": "codex_cli", **self.receipt})
                status = self.receipt["status"]
                self.emit(
                    "warning" if status != "available" else "progress",
                    {
                        "message": (
                            "Không đọc được hạn mức Codex CLI; trạng thái chưa xác định, không đổi model hay tự mua thêm lượt."
                            if status == "unavailable"
                            else "Codex CLI đã hết hạn mức; dừng trước request mới để tránh tiêu DeepSeek cho lượt không hoàn tất."
                            if status == "exhausted"
                            else "Đã kiểm tra hạn mức Codex CLI; đây là snapshot, không bảo đảm đủ dung lượng cho cả lượt."
                        )
                    },
                )
            if self.receipt["status"] == "exhausted":
                raise ValueError(
                    "Codex CLI đã hết hạn mức. Không gọi model mới hoặc tự chuyển sang DeepSeek; thử lại sau khi hạn mức được cấp lại."
                )
