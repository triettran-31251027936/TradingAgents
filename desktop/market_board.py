"""Read-only Vietnam market board: real OHLCV and reported matched trades."""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, time as daytime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from desktop.vietnam import CHART, _request, load, symbol, write_json

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TRADES = "https://trading.vietcap.com.vn/api/market-watch/LEData/getAll"
LOCK = threading.Lock()
INTERVALS = {"1m": ("ONE_MINUTE", 60, 300), "1d": ("ONE_DAY", 86400, 180)}


def phase(now, exchange="HOSE"):
    now = now.astimezone(TZ)
    clock = now.time()
    if now.weekday() >= 5:
        return "closed"
    start = daytime(9, 15) if exchange == "HOSE" else daytime(9, 0)
    end = daytime(15, 0) if exchange == "UPCOM" else daytime(14, 30)
    if start <= clock < daytime(11, 30) or daytime(13) <= clock < end:
        return "continuous"
    if daytime(11, 30) <= clock < daytime(13):
        return "lunch"
    if daytime(9) <= clock < start or end <= clock < daytime(14, 45):
        return "auction"
    return "closed"


def normalize(raw, interval, now):
    if interval not in INTERVALS:
        raise ValueError("Chỉ hỗ trợ nến 1 phút hoặc 1 ngày.")
    items = raw[0] if isinstance(raw, list) and raw else raw
    fields = ("t", "o", "h", "l", "c", "v")
    if not isinstance(items, dict) or not all(isinstance(items.get(k), list) for k in fields):
        raise ValueError("Schema OHLCV không hợp lệ.")
    if len({len(items[k]) for k in fields}) != 1:
        raise ValueError("Cột OHLCV khác độ dài.")
    rows = []
    seen = set()
    for stamp, *prices in zip(*(items[k] for k in fields), strict=True):
        if isinstance(stamp, bool):
            raise ValueError("Timestamp không hợp lệ.")
        stamp = int(stamp)
        if stamp in seen:
            raise ValueError("Nến trùng timestamp.")
        seen.add(stamp)
        if stamp > int(now.timestamp()):
            continue
        numbers = [float(v) for v in prices]
        if any(not math.isfinite(v) for v in numbers) or min(numbers[:4]) <= 0 or numbers[-1] < 0:
            raise ValueError("Nến có số liệu không hợp lệ.")
        o, h, low, c, v = numbers
        if not low <= min(o, c) <= max(o, c) <= h:
            raise ValueError("High/low không nhất quán.")
        if interval == "1m":
            completed = stamp + 60 <= now.timestamp()
        else:
            day = datetime.fromtimestamp(stamp, TZ).date()
            completed = day < now.astimezone(TZ).date() or now.astimezone(TZ).time() >= daytime(15)
        rows.append(
            {
                "time": stamp,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": v,
                "complete": completed,
            }
        )
    rows.sort(key=lambda r: r["time"])
    if not rows:
        raise ValueError("Không có nến thị trường thật.")
    return rows


def normalize_trades(raw, now):
    items = raw.get("data", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("Schema giao dịch không hợp lệ.")
    result = []
    seen = set()
    for row in items:
        try:
            stamp = int(float(row["truncTime"]))
            price = float(row["matchPrice"])
            volume = float(row["matchVol"])
            key = str(row["id"])
        except (KeyError, ValueError, TypeError):
            continue
        if (
            key in seen
            or stamp > now.timestamp()
            or min(price, volume) <= 0
            or not math.isfinite(price + volume)
        ):
            continue
        seen.add(key)
        result.append(
            {
                "id": key,
                "time": stamp,
                "price": price,
                "volume": volume,
                "side": row.get("matchType"),
            }
        )
    return sorted(result, key=lambda r: r["time"], reverse=True)


def board(ticker, interval, cache_dir, *, now=None, session=None, refresh=False):
    name = symbol(ticker)
    now = now or datetime.now(TZ)
    if interval not in INTERVALS:
        raise ValueError("Khung thời gian không hỗ trợ.")
    folder = Path(cache_dir) / "market-board"
    path = folder / f"{name}-{interval}.json"
    with LOCK:
        cached = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
        if cached and not refresh and now.timestamp() - cached["retrieved_epoch"] < 20:
            return cached
        session = session or requests.Session()
        session.headers.update(
            {
                "User-Agent": "TradingAgents-Research/0.8",
                "Accept": "application/json",
                "Referer": "https://trading.vietcap.com.vn/",
            }
        )
        try:
            frame, _, count = INTERVALS[interval]
            raw = _request(
                session,
                CHART,
                {
                    "timeFrame": frame,
                    "symbols": [name],
                    "to": int(now.timestamp()),
                    "countBack": count,
                },
            )
            rows = normalize(raw, interval, now)
            trade_error = None
            try:
                trades = normalize_trades(
                    _request(session, TRADES, {"symbol": name, "limit": 30, "truncTime": None}), now
                )
            except (requests.RequestException, ValueError) as exc:
                trades = []
                trade_error = type(exc).__name__
            daily = load(
                name + ".VN", now.astimezone(TZ).date().isoformat(), cache_dir, prices_only=True
            )
            reference = daily["prices"][-1]
            today = now.astimezone(TZ).date().isoformat()
            # A completed current-day candle is not the prior reference close.
            previous = next((r for r in reversed(daily["prices"]) if r["date"] < today), reference)
            quote = (
                trades[0]
                if trades
                else {"time": rows[-1]["time"], "price": rows[-1]["close"], "volume": None}
            )
            age = max(0, now.timestamp() - quote["time"])
            exchange = daily.get("exchange")
            current_phase = phase(now, exchange)
            data_status = (
                "delayed"
                if current_phase == "continuous" and age > 180
                else "recent"
                if current_phase == "continuous"
                else current_phase
            )
            result = {
                "ticker": name + ".VN",
                "exchange": exchange,
                "currency": "VND",
                "interval": interval,
                "candles": rows,
                "completed_sessions": [r["date"] for r in daily["prices"]],
                "trades": trades,
                "quote": quote,
                "reference_close": previous["close"],
                "reference_date": previous["date"],
                "change": quote["price"] - previous["close"],
                "change_fraction": quote["price"] / previous["close"] - 1,
                "retrieved_at": now.isoformat(),
                "retrieved_epoch": now.timestamp(),
                "quote_age_seconds": round(age),
                "status": data_status,
                "source": "Vietcap public market data",
                "source_urls": [CHART, TRADES],
                "transport": "REST polling, not a guaranteed realtime exchange feed",
                "trades_error": trade_error,
                "warnings": [
                    "Ngày nghỉ/lịch giao dịch đặc biệt chưa có lịch chính thức; giờ hiển thị là khung giờ dự kiến.",
                    "Giá trên bảng là dữ liệu báo về, không phải báo giá khớp lệnh được bảo đảm.",
                ],
            }
            write_json(path, result)
            return result
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            if cached:
                return {
                    **cached,
                    "status": "unavailable",
                    "error": type(exc).__name__,
                    "warnings": cached.get("warnings", [])
                    + ["Không cập nhật được nguồn; đang hiển thị dữ liệu cũ với timestamp gốc."],
                }
            raise RuntimeError("Chưa lấy được bảng giá thật; không tạo dữ liệu giả.") from None
