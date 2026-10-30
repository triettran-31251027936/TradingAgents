"""Consent-gated 3M VND account, matched by BackBroker on later real minute bars.

This is local paper trading, not an exchange UAT. No code sends a real order.
The legacy daily replay database is kept separately and never reset.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, time as daytime, timedelta
from zoneinfo import ZoneInfo

import backtrader as bt
import pandas as pd

from desktop.market_board import phase
from desktop.paper import Store, VNCommission, _utc, validate_order
from desktop.vietnam import digest

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
STARTING_CASH = 3_000_000


def local(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("Thời gian phải có múi giờ.")
    return dt.astimezone(TZ)


def run_broker(orders, frames, now, exchanges, initial_cash=STARTING_CASH):
    """Rebuild from frozen completed bars; an order is created after human approval."""
    now = local(now)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.addcommissioninfo(VNCommission())
    feeds = {}
    sessions = set()
    results = {}
    eligible = {}
    for ticker, rows in frames.items():
        rows = [r for r in rows if r["time"] + 60 <= now.timestamp() and r.get("complete", True)]
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        index = (
            pd.to_datetime(frame.pop("time") + 60, unit="s", utc=True)
            .dt.tz_convert(TZ)
            .dt.tz_localize(None)
        )
        frame.index = pd.DatetimeIndex(index)
        frame = frame[["open", "high", "low", "close", "volume"]]
        sessions.update(
            x.date().isoformat()
            for x in frame.index
            if x.date() < now.date() or now.time() >= daytime(15)
        )
        feeds[ticker] = cerebro.adddata(
            bt.feeds.PandasData(dataname=frame, timeframe=bt.TimeFrame.Minutes, compression=1),
            name=ticker,
        )
    if not feeds:
        return {
            "cash": initial_cash,
            "nav": initial_cash,
            "positions": {},
            "orders": {},
            "sessions": [],
        }

    def filler(order, price, ago):
        end = order.data.datetime.datetime(ago).replace(tzinfo=TZ)
        # Never match auctions or fabricated/missing minutes.
        if (
            phase(
                end.replace(second=0) - pd.Timedelta(seconds=1),
                exchanges.get(order.data._name, "HOSE"),
            )
            != "continuous"
        ):
            return 0
        capacity = math.floor(float(order.data.volume[ago]) * 0.01 / 100) * 100
        return min(abs(order.executed.remsize), max(0, capacity))

    cerebro.broker.set_filler(filler)

    class Strategy(bt.Strategy):
        def __init__(self):
            self.sent = set()

        def prenext(self):
            self.next()

        def next(self):
            for order in orders:
                data = feeds.get(order["ticker"])
                if data is None or not len(data) or order["id"] in self.sent:
                    continue
                end = data.datetime.datetime(0).replace(tzinfo=TZ)
                if end < local(order["approved_at"]):
                    continue
                if phase(end - pd.Timedelta(seconds=1), order["exchange"]) != "continuous":
                    continue
                expiry = datetime.combine(
                    end.date(), daytime(14, 45) if order["exchange"] != "UPCOM" else daytime(15)
                )
                method = self.buy if order["side"] == "Buy" else self.sell
                placed = method(
                    data=data,
                    size=order["quantity"],
                    price=order["limit_price"],
                    exectype=bt.Order.Limit,
                    valid=expiry,
                )
                placed.addinfo(paper_id=order["id"])
                self.sent.add(order["id"])
                eligible[order["id"]] = end.date().isoformat()

        def notify_order(self, order):
            key = order.info.get("paper_id")
            if key is None:
                return
            filled = bool(order.executed.size)
            stamp = bt.num2date(order.executed.dt).replace(tzinfo=TZ) if filled else None
            results[key] = {
                "state": order.getstatusname().upper(),
                "filled_quantity": int(abs(order.executed.size)),
                "fill_price": float(order.executed.price) if filled else None,
                "fee": float(order.executed.comm),
                "fill_date": stamp.date().isoformat() if stamp else None,
                "fill_time": stamp.isoformat() if stamp else None,
            }

    cerebro.addstrategy(Strategy)
    strategy = cerebro.run(runonce=False, preload=True)[0]
    positions = {}
    for ticker, data in feeds.items():
        held = strategy.getposition(data)
        if held.size:
            positions[ticker] = {
                "quantity": int(held.size),
                "average_price": float(held.price),
                "mark_price": float(data.close[0]),
                "market_value": int(held.size) * float(data.close[0]),
            }
    for order in orders:
        row = results.setdefault(
            order["id"],
            {
                "state": "APPROVED",
                "filled_quantity": 0,
                "fill_price": None,
                "fee": 0,
                "fill_date": None,
                "fill_time": None,
            },
        )
        if row["state"] == "COMPLETED":
            row["state"] = "FILLED"
        elif row["state"] in ("SUBMITTED", "ACCEPTED", "PARTIAL"):
            row["state"] = "APPROVED"
        day = eligible.get(order["id"])
        close = daytime(15) if order["exchange"] == "UPCOM" else daytime(14, 45)
        if (
            day
            and (
                now.date().isoformat() > day
                or now.date().isoformat() == day
                and now.time() >= close
            )
            and row["filled_quantity"] < order["quantity"]
            and row["state"] not in ("MARGIN", "REJECTED", "CANCELED")
        ):
            row["state"] = "PARTIAL_EXPIRED" if row["filled_quantity"] else "EXPIRED"
    return {
        "cash": float(cerebro.broker.getcash()),
        "nav": float(cerebro.broker.getvalue()),
        "positions": positions,
        "orders": results,
        "sessions": sorted(sessions),
    }


class PracticeStore(Store):
    max_position_fraction = 1.0

    def __init__(self, cache_dir):
        super().__init__(cache_dir, filename="practice.sqlite", initial_cash=STARTING_CASH)
        with self.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS minute_bars(ticker TEXT NOT NULL,time INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(ticker,time));
            CREATE TABLE IF NOT EXISTS marks(ticker TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processed_approvals(order_id TEXT PRIMARY KEY);""")

    def snapshot(self, db=None):
        book = super().snapshot(db)
        book.update(
            mode="local_backtrader_live_minutes",
            engine="Backtrader / BackBroker · real completed minute bars",
            starting_capital=STARTING_CASH,
            board_lot=100,
            allow_margin=False,
            policy="Practice-only allocation can use up to100% of this3M virtual account. Whole lots100; no fractional or fake odd-lot liquidity. No deposits or real orders.",
        )
        book["fingerprint"] = digest(
            {k: v for k, v in book.items() if k not in ("orders", "fingerprint")}
        )
        return book

    def proposal(self, report_id, decision, bundle, exchange):
        intent = decision["execution"]
        if intent["action"] == "Hold":
            return {"status": "NO_ORDER", "reason": intent["rationale"], "decision": decision}
        today = datetime.now(TZ).date().isoformat()
        if bundle["analysis_date"] != today:
            raise ValueError(
                "Lượt quá khứ chỉ để nghiên cứu; không chuyển decision lịch sử thành lệnh theo thị trường hiện tại."
            )
        if exchange not in ("HOSE", "HNX", "UPCOM"):
            raise ValueError("Chưa xác minh được sàn Việt Nam.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM orders WHERE report_id=?", (report_id,)
            ).fetchone()
            if existing:
                return self.preview(existing["id"], db)
            book = self.snapshot(db)
            order = {
                "id": uuid.uuid4().hex,
                "ticker": bundle["ticker"],
                "side": intent["action"],
                "quantity": intent["quantity"],
                "limit_price": intent["limit_price"],
                "exchange": exchange,
                "asof": today,
                "analysis_date": bundle["analysis_date"],
                "quote_date": bundle["prices"][-1]["date"],
                "reference_close": bundle["prices"][-1]["close"],
                "quote_fingerprint": bundle["fingerprint"],
                "rationale": intent["rationale"],
                "data_gaps": intent["data_gaps"],
                "mode": "local_backtrader_live_minutes",
            }
            validate_order(order, book)
            for key in ("close", "adv20", "volatility20_annual", "historical_var95_1d"):
                if bundle["metrics"][key]["value"] is None:
                    raise ValueError("Thiếu dữ liệu định lượng trọng yếu; chưa tạo lệnh.")
            db.execute(
                "INSERT INTO orders VALUES(?,?,?,?,?,NULL,NULL)",
                (order["id"], report_id, json.dumps(order, ensure_ascii=False), "PROPOSED", _utc()),
            )
            db.execute(
                "INSERT INTO audit(time,event,order_id,details) VALUES(?,?,?,?)",
                (
                    _utc(),
                    "PROPOSED",
                    order["id"],
                    json.dumps({"report_id": report_id, "decision": decision}, ensure_ascii=False),
                ),
            )
            return self.preview(order["id"], db)

    def preview(self, order_id, db=None):
        preview = super().preview(order_id, db)
        preview["matching"] = (
            "TIỀN ẢO · BackBroker: chỉ nến phút đã đóng sau lúc bạn duyệt; tối đa1% volume phút, lô100; bỏ phiên đấu giá; hết hạn cuối phiên đủ điều kiện đầu tiên. Không phải lệnh tại sàn thật."
        )
        preview["starting_capital"] = STARTING_CASH
        preview["preview_hash"] = digest({k: v for k, v in preview.items() if k != "preview_hash"})
        return preview

    def approve(self, order_id, preview_hash, approval, *, now=None):
        preview = self.preview(order_id)
        if preview["order"]["analysis_date"] != datetime.now(TZ).date().isoformat():
            raise ValueError("Đề xuất đã sang ngày mới; cần PM phân tích và tạo đề xuất mới.")
        result = super().approve(order_id, preview_hash, approval, now=now)
        result["message"] = (
            "Đã duyệt lệnh TIỀN ẢO. Chờ nến phút thật sau thời điểm duyệt; chưa có lệnh tại sàn thật."
        )
        return result

    def advance(self, *args, **kwargs):
        raise ValueError(
            "Tài khoản3 triệu dùng nến phút thị trường thật; không chạy lại bằng đồng hồ replay ngày."
        )

    def sync(self, boards, *, now=None):
        now = local(now or datetime.now(TZ))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            # Revisions block matching; never overwrite bars previously consumed.
            changed = False
            for ticker, market in boards.items():
                if market.get("status") == "unavailable":
                    continue
                for bar in market["candles"]:
                    if not bar.get("complete") or bar["time"] + 60 > now.timestamp():
                        continue
                    frozen = db.execute(
                        "SELECT payload FROM minute_bars WHERE ticker=? AND time=?",
                        (ticker, bar["time"]),
                    ).fetchone()
                    if frozen:
                        if digest(json.loads(frozen["payload"])) != digest(bar):
                            raise ValueError(
                                "Nến phút đã thay đổi; giữ tài khoản để đối soát trước khi khớp tiếp."
                            )
                    else:
                        db.execute(
                            "INSERT INTO minute_bars VALUES(?,?,?)",
                            (ticker, bar["time"], json.dumps(bar)),
                        )
                        changed = True
                quote = market["quote"]
                old = db.execute("SELECT payload FROM marks WHERE ticker=?", (ticker,)).fetchone()
                mark = {
                    "price": quote["price"],
                    "time": quote["time"],
                    "exchange": market["exchange"],
                }
                if old is None or (
                    mark["time"] >= json.loads(old["payload"])["time"]
                    and json.loads(old["payload"]) != mark
                ):
                    db.execute(
                        "INSERT OR REPLACE INTO marks VALUES(?,?)", (ticker, json.dumps(mark))
                    )
                    changed = True
            orders = [
                {**json.loads(r["payload"]), "approved_at": r["approved_at"]}
                for r in db.execute(
                    "SELECT * FROM orders WHERE approved_at IS NOT NULL ORDER BY approved_at,id"
                )
            ]
            for order in orders:
                if not db.execute(
                    "SELECT 1 FROM processed_approvals WHERE order_id=?", (order["id"],)
                ).fetchone():
                    changed = True
            # End-of-session expiry must run even when the exchange sends no new bars.
            if any(o["status"] == "APPROVED" for o in self.snapshot(db)["orders"]):
                changed = True
            if not changed:
                return self.snapshot(db)
            frames = {}
            exchanges = {}
            for row in db.execute("SELECT ticker,payload FROM minute_bars ORDER BY time"):
                frames.setdefault(row["ticker"], []).append(json.loads(row["payload"]))
            for row in db.execute("SELECT ticker,payload FROM marks"):
                exchanges[row["ticker"]] = json.loads(row["payload"])["exchange"]
            for order in orders:
                rows = frames.get(order["ticker"], [])
                approved = local(order["approved_at"])
                relevant = [r for r in rows if r["time"] + 60 >= approved.timestamp()]
                if relevant:
                    cursor = approved.replace(second=0, microsecond=0)
                    # A bounded endpoint must not silently skip an offline trading session.
                    for row in relevant:
                        endpoint = datetime.fromtimestamp(row["time"], TZ)
                        if endpoint - cursor > timedelta(days=7):
                            raise ValueError("Thiếu lịch sử sau duyệt; dừng khớp để đối soát.")
                        missing = 0
                        while cursor < endpoint:
                            if phase(cursor, order["exchange"]) == "continuous":
                                missing += 1
                            cursor += timedelta(minutes=1)
                        if missing > 5:
                            raise ValueError(
                                "Thiếu quá 5 phút giao dịch liên tục sau duyệt; chưa khớp tiếp."
                            )
                        cursor = endpoint + timedelta(minutes=1)
            result = run_broker(orders, frames, now, exchanges, before["initial_cash"])
            previous_state = json.loads(before["state"])
            result["sessions"] = sorted(
                set(result["sessions"])
                | set(previous_state.get("sessions", []))
                | {
                    day
                    for market in boards.values()
                    for day in market.get("completed_sessions", [])
                    if day < now.date().isoformat() or now.time() >= daytime(15)
                }
            )
            # Mark held shares to the latest real trade, while fills only use final bars.
            for ticker, p in result["positions"].items():
                row = db.execute("SELECT payload FROM marks WHERE ticker=?", (ticker,)).fetchone()
                if row:
                    mark = json.loads(row["payload"])
                    p.update(
                        mark_price=mark["price"],
                        market_value=p["quantity"] * mark["price"],
                        mark_time=mark["time"],
                    )
            result["nav"] = result["cash"] + sum(
                p["market_value"] for p in result["positions"].values()
            )
            for order in orders:
                if order["ticker"] not in frames:
                    raise ValueError("Thiếu nến của lệnh đã duyệt; không tự tái tạo giao dịch.")
                existing = json.loads(
                    db.execute("SELECT result FROM orders WHERE id=?", (order["id"],)).fetchone()[
                        "result"
                    ]
                    or "{}"
                )
                current = result["orders"][order["id"]]
                if current["filled_quantity"] < existing.get("filled_quantity", 0):
                    raise ValueError("Thiếu lịch sử khớp gốc; dừng đối soát.")
                if (
                    existing.get("filled_quantity")
                    and existing["filled_quantity"] == current["filled_quantity"]
                    and any(
                        existing.get(k) != current.get(k)
                        for k in ("fill_price", "fee", "fill_time")
                    )
                ):
                    raise ValueError("Kết quả khớp gốc thay đổi; dừng đối soát.")
                db.execute(
                    "UPDATE orders SET status=?,result=? WHERE id=?",
                    (current["state"], json.dumps(current), order["id"]),
                )
                db.execute("INSERT OR IGNORE INTO processed_approvals VALUES(?)", (order["id"],))
            db.execute(
                "UPDATE account SET clock=?,version=version+1,state=? WHERE id=1",
                (now.date().isoformat(), json.dumps(result)),
            )
            db.execute(
                "INSERT INTO audit(time,event,details) VALUES(?,?,?)",
                (
                    _utc(),
                    "MARKET_SYNC",
                    json.dumps(
                        {
                            "ticker_count": len(boards),
                            "source": "real completed minute OHLCV",
                            "human_approved_orders": len(orders),
                        }
                    ),
                ),
            )
        return self.snapshot()
