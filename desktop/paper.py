"""Local Backtrader VN daily-bar replay. There is no live broker transport.

An order is immutable, reviewed in a native dialog, and authorized with a
short-lived HMAC from the Electron main process. Approval submits a virtual
limit order; only a later real dataset bar may fill it. No fabricated ticks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import backtrader as bt
import pandas as pd

from desktop.vietnam import digest

INITIAL_CASH = 100_000_000


def _utc():
    return datetime.now(UTC).isoformat()


def tick(exchange, price):
    if exchange == "HOSE":
        return 10 if price < 10000 else 50 if price < 50000 else 100
    if exchange in ("HNX", "UPCOM"):
        return 100
    raise ValueError("Chưa xác minh được sàn; không đoán bước giá.")


def validate_order(order, book):
    quantity = order["quantity"]
    price = order["limit_price"]
    if type(quantity) is not int or quantity <= 0 or quantity % 100:
        raise ValueError("Lệnh tiền ảo cần số cổ phiếu nguyên, bội số100.")
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError("Giá giới hạn phải là số VND/cổ phiếu dương.")
    step = tick(order["exchange"], price)
    if abs(price / step - round(price / step)) > 1e-8:
        raise ValueError(f"Giá không đúng bước giá {step} VND của sàn.")
    if order["side"] not in ("Buy", "Sell"):
        raise ValueError("Hold không tạo lệnh.")
    if order["side"] == "Buy":
        required = quantity * price * (1 + book["commission"])
        if required > book["available_cash"] + 1e-6:
            raise ValueError("Không đủ tiền ảo khả dụng sau phí và các lệnh đã duyệt.")
        current = book["positions"].get(order["ticker"], {}).get("market_value", 0) + book.get(
            "reserved_buy_value", {}
        ).get(order["ticker"], 0)
        if current + quantity * price > book["nav"] * book["max_position_fraction"] + 1e-6:
            raise ValueError("Lệnh vượt giới hạn tỷ trọng mô phỏng của tài khoản.")
    elif quantity > book["positions"].get(order["ticker"], {}).get("available_quantity", 0):
        raise ValueError("Không đủ cổ phiếu đã về tài khoản; không bán khống/bán trước T+2.")


class VNCommission(bt.CommInfoBase):
    params = (
        ("commission", 0.0015),
        ("sell_tax", 0.001),
        ("stocklike", True),
        ("commtype", bt.CommInfoBase.COMM_PERC),
        ("percabs", True),
    )

    def _getcommission(self, size, price, pseudoexec):
        return abs(size) * price * (self.p.commission + (self.p.sell_tax if size < 0 else 0))


def replay(
    orders,
    frames,
    initial_cash,
    clock,
    *,
    commission=0.0015,
    sell_tax=0.001,
    max_volume_fraction=0.01,
):
    """Deterministic actual BackBroker matching, always next-bar (no cheat mode)."""
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.addcommissioninfo(VNCommission(commission=commission, sell_tax=sell_tax))
    feeds = {}
    calendar = set()
    for ticker, rows in frames.items():
        rows = [r for r in rows if r["date"] <= clock]
        if not rows:
            raise ValueError("Thiếu nến của tài khoản mô phỏng.")
        frame = pd.DataFrame(rows)
        frame.index = pd.to_datetime(frame.pop("date"))
        calendar.update(x.date().isoformat() for x in frame.index)
        feeds[ticker] = cerebro.adddata(bt.feeds.PandasData(dataname=frame), name=ticker)

    def filler(order, price, ago):
        available = math.floor(float(order.data.volume[ago]) * max_volume_fraction / 100) * 100
        return min(abs(order.executed.remsize), max(0, available))

    cerebro.broker.set_filler(filler)
    results = {}

    class Strategy(bt.Strategy):
        def __init__(self):
            self.sent = set()

        def prenext(self):
            self.next()

        def next(self):
            for order in orders:
                data = feeds[order["ticker"]]
                if not len(data) or order["id"] in self.sent:
                    continue
                if data.datetime.date(0).isoformat() != order["asof"]:
                    continue
                subsequent = [
                    r["date"] for r in frames[order["ticker"]] if order["asof"] < r["date"] <= clock
                ]
                valid = datetime.fromisoformat(subsequent[0] + "T23:59:59") if subsequent else None
                method = self.buy if order["side"] == "Buy" else self.sell
                placed = method(
                    data=data,
                    size=order["quantity"],
                    price=order["limit_price"],
                    exectype=bt.Order.Limit,
                    valid=valid,
                )
                placed.addinfo(paper_id=order["id"])
                self.sent.add(order["id"])

        def notify_order(self, order):
            key = order.info.get("paper_id")
            if key is None:
                return
            status = order.getstatusname().upper()
            result = {
                "state": status,
                "filled_quantity": int(abs(order.executed.size)),
                "fill_price": float(order.executed.price) if order.executed.size else None,
                "fee": float(order.executed.comm),
                "fill_date": bt.num2date(order.executed.dt).date().isoformat()
                if order.executed.size
                else None,
            }
            results[key] = result

    cerebro.addstrategy(Strategy)
    strategy = cerebro.run(runonce=False, preload=True)[0]
    positions = {}
    for ticker, feed in feeds.items():
        held = strategy.getposition(feed)
        if held.size:
            positions[ticker] = {
                "quantity": int(held.size),
                "average_price": float(held.price),
                "mark_price": float(feed.close[0]),
                "market_value": int(held.size) * float(feed.close[0]),
            }
    for order in orders:
        result = results.setdefault(
            order["id"],
            {
                "state": "APPROVED",
                "filled_quantity": 0,
                "fill_price": None,
                "fee": 0,
                "fill_date": None,
            },
        )
        subsequent = [r for r in frames[order["ticker"]] if order["asof"] < r["date"] <= clock]
        if (
            subsequent
            and result["filled_quantity"] < order["quantity"]
            and result["state"] not in ("MARGIN", "REJECTED", "CANCELED")
        ):
            result["state"] = "PARTIAL_EXPIRED" if result["filled_quantity"] else "EXPIRED"
        elif result["state"] == "COMPLETED":
            result["state"] = "FILLED"
        elif result["state"] in ("ACCEPTED", "SUBMITTED"):
            result["state"] = "APPROVED"
    return {
        "cash": float(cerebro.broker.getcash()),
        "nav": float(cerebro.broker.getvalue()),
        "positions": positions,
        "orders": results,
        "sessions": sorted(calendar),
    }


class Store:
    def __init__(self, cache_dir, *, filename="backtrader.sqlite", initial_cash=INITIAL_CASH):
        folder = Path(cache_dir) / "paper"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if filename not in ("backtrader.sqlite", "practice.sqlite"):
            raise ValueError("Tên tài khoản không hợp lệ.")
        self.path = folder / filename
        if self.path.is_symlink():
            raise ValueError("Không mở tài khoản qua file liên kết.")
        with self.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL NOT NULL, clock TEXT, version INTEGER NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, report_id TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, approved_at TEXT, result TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS report_once ON orders(report_id);
            CREATE TABLE IF NOT EXISTS consumed_bars(ticker TEXT NOT NULL, day TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(ticker,day));
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, event TEXT NOT NULL, order_id TEXT, details TEXT NOT NULL);""")
            db.execute(
                "INSERT OR IGNORE INTO account VALUES(1,?,NULL,0,?)",
                (
                    initial_cash,
                    json.dumps(
                        {
                            "cash": initial_cash,
                            "nav": initial_cash,
                            "positions": {},
                            "orders": {},
                            "sessions": [],
                        }
                    ),
                ),
            )
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def snapshot(self, db=None):
        own = db is None
        db = db or self.connect()
        try:
            row = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            state = json.loads(row["state"])
            positions = state["positions"]
            cash = state["cash"]
            orders = [dict(r) for r in db.execute("SELECT * FROM orders ORDER BY created_at")]
            sessions = state.get("sessions", [])
            clock = row["clock"]
            locks = 0
            reserved_buy = {}
            for item in orders:
                order = json.loads(item["payload"])
                result = json.loads(item["result"]) if item["result"] else {}
                filled = result.get("filled_quantity", 0)
                fill_date = result.get("fill_date")
                matured = (
                    fill_date is None
                    or len([d for d in sessions if fill_date < d <= str(clock)]) >= 2
                )
                if not matured and filled:
                    if order["side"] == "Sell":
                        cash -= filled * result["fill_price"] - result["fee"]
                    elif order["ticker"] in positions:
                        positions[order["ticker"]]["unsettled_quantity"] = (
                            positions[order["ticker"]].get("unsettled_quantity", 0) + filled
                        )
                if item["status"] == "APPROVED":
                    remaining = order["quantity"] - filled
                    if order["side"] == "Buy":
                        locks += remaining * order["limit_price"] * 1.0015
                        reserved_buy[order["ticker"]] = (
                            reserved_buy.get(order["ticker"], 0) + remaining * order["limit_price"]
                        )
                    elif order["ticker"] in positions:
                        positions[order["ticker"]]["reserved_quantity"] = (
                            positions[order["ticker"]].get("reserved_quantity", 0) + remaining
                        )
            for p in positions.values():
                p["available_quantity"] = max(
                    0,
                    p["quantity"] - p.get("unsettled_quantity", 0) - p.get("reserved_quantity", 0),
                )
            book = {
                "mode": "local_backtrader_daily_replay",
                "engine": "Backtrader 1.9.78.123 / BackBroker",
                "initial_cash": row["initial_cash"],
                "clock": clock,
                "version": row["version"],
                "cash": state["cash"],
                "available_cash": max(0, cash - locks),
                "nav": state["nav"],
                "positions": positions,
                "reserved_buy_value": reserved_buy,
                "commission": 0.0015,
                "sell_tax": 0.001,
                "max_position_fraction": getattr(self, "max_position_fraction", 0.20),
                "settlement": "Available after two subsequent observed trading sessions (conservative end-of-day T+2 model).",
                "orders": [
                    {
                        "id": o["id"],
                        "status": o["status"],
                        "approved_at": o["approved_at"],
                        "created_at": o["created_at"],
                        **json.loads(o["payload"]),
                        "result": json.loads(o["result"]) if o["result"] else None,
                    }
                    for o in orders
                ],
            }
            book["fingerprint"] = digest({k: v for k, v in book.items() if k != "orders"})
            return book
        finally:
            if own:
                db.close()

    def proposal(self, report_id, decision, bundle, exchange):
        intent = decision["execution"]
        if intent["action"] == "Hold":
            return {"status": "NO_ORDER", "reason": intent["rationale"], "decision": decision}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM orders WHERE report_id=?", (report_id,)
            ).fetchone()
            if existing:
                return self.preview(existing["id"], db)
            row = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            quote_day = bundle["prices"][-1]["date"]
            if row["clock"] is None:
                db.execute("UPDATE account SET clock=?,version=version+1 WHERE id=1", (quote_day,))
            book = self.snapshot(db)
            if book["clock"] != quote_day:
                raise ValueError(
                    "Ngày tài khoản replay khác ngày giá; tiến phiên trước khi tạo lệnh mới."
                )
            order = {
                "id": uuid.uuid4().hex,
                "ticker": bundle["ticker"],
                "side": intent["action"],
                "quantity": intent["quantity"],
                "limit_price": intent["limit_price"],
                "exchange": exchange,
                "asof": book["clock"],
                "analysis_date": bundle["analysis_date"],
                "quote_date": quote_day,
                "reference_close": bundle["prices"][-1]["close"],
                "quote_fingerprint": bundle["fingerprint"],
                "rationale": intent["rationale"],
                "data_gaps": intent["data_gaps"],
                "mode": "local_backtrader_daily_replay",
            }
            validate_order(order, book)
            # No automatic trade on incomplete core price/volume/risk evidence.
            required = ("close", "adv20", "volatility20_annual", "historical_var95_1d")
            if any(bundle["metrics"][k]["value"] is None for k in required):
                raise ValueError(
                    "Thiếu dữ liệu định lượng trọng yếu; chỉ lưu decision, chưa tạo lệnh."
                )
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
        own = db is None
        db = db or self.connect()
        try:
            row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise ValueError("Lệnh tiền ảo không tồn tại.")
            order = json.loads(row["payload"])
            book = self.snapshot(db)
            preview = {
                "id": order_id,
                "status": row["status"],
                "order": order,
                "book_fingerprint": book["fingerprint"],
                "account_version": book["version"],
                "notional": order["quantity"] * order["limit_price"],
                "estimated_fee": order["quantity"]
                * order["limit_price"]
                * (book["commission"] + (book["sell_tax"] if order["side"] == "Sell" else 0)),
                "available_cash": book["available_cash"],
                "nav": book["nav"],
                "matching": "Virtual limit, at most1% of next observed daily volume, expires after first eligible session. No immediate fill or real exchange order.",
                "settlement": book["settlement"],
                "human_approval_required": True,
            }
            preview["preview_hash"] = digest(preview)
            return preview
        finally:
            if own:
                db.close()

    def approve(self, order_id, preview_hash, approval, *, now=None):
        # The renderer cannot manufacture approval: the signing key exists only
        # in Electron main and the short-lived approval child environment.
        secret = os.environ.get("TRADINGAGENTS_PAPER_APPROVAL_KEY")
        if not secret or not isinstance(approval, dict):
            raise ValueError("Cần xác nhận lệnh qua hộp thoại người dùng.")
        timestamp = approval.get("timestamp")
        signature = approval.get("signature")
        if (
            type(timestamp) is not int
            or abs((time.time() if now is None else now) - timestamp) > 300
        ):
            raise ValueError("Xác nhận đã hết hạn; cần duyệt lại.")
        message = f"{order_id}|{preview_hash}|{timestamp}"
        expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
            raise ValueError("Xác nhận không hợp lệ.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            preview = self.preview(order_id, db)
            if preview["status"] != "PROPOSED":
                raise ValueError("Lệnh đã được xử lý; không gửi trùng.")
            if not hmac.compare_digest(preview["preview_hash"], preview_hash):
                raise ValueError("Danh mục/lệnh đã thay đổi; cần xem và duyệt lại.")
            validate_order(preview["order"], self.snapshot(db))
            db.execute(
                "UPDATE orders SET status=?,approved_at=? WHERE id=?",
                ("APPROVED", _utc(), order_id),
            )
            db.execute("UPDATE account SET version=version+1 WHERE id=1")
            db.execute(
                "INSERT INTO audit(time,event,order_id,details) VALUES(?,?,?,?)",
                (
                    _utc(),
                    "HUMAN_APPROVED",
                    order_id,
                    json.dumps({"preview_hash": preview_hash, "approved_at": timestamp}),
                ),
            )
        return {
            "id": order_id,
            "status": "APPROVED",
            "message": "Đã gửi lệnh tiền ảo. Chờ nến giao dịch kế tiếp để khớp; chưa có giao dịch thật.",
        }

    def reject(self, order_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None or row["status"] != "PROPOSED":
                raise ValueError("Chỉ từ chối được lệnh chưa duyệt.")
            db.execute("UPDATE orders SET status=? WHERE id=?", ("REJECTED", order_id))
            db.execute(
                "INSERT INTO audit(time,event,order_id,details) VALUES(?,?,?,?)",
                (_utc(), "HUMAN_REJECTED", order_id, "{}"),
            )
        return {"id": order_id, "status": "REJECTED"}

    def advance(self, target, frames):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            if account["clock"] and target < account["clock"]:
                raise ValueError("Không thể lùi đồng hồ replay.")
            orders = [
                json.loads(r["payload"])
                for r in db.execute("SELECT payload FROM orders WHERE approved_at IS NOT NULL")
            ]
            for o in orders:
                rows = frames.get(o["ticker"], [])
                if not rows or rows[0]["date"] > o["asof"]:
                    raise ValueError(
                        "Thiếu lịch sử gốc; không tái tạo danh mục bằng dữ liệu bị cắt."
                    )
            if not frames:
                raise ValueError("Chưa có mã để cập nhật giá tài khoản.")
            for stored in db.execute("SELECT * FROM consumed_bars"):
                rows = {r["date"]: r for r in frames.get(stored["ticker"], [])}
                if (
                    stored["day"] not in rows
                    or digest(rows[stored["day"]]) != stored["fingerprint"]
                ):
                    raise ValueError(
                        "Lịch sử giá đã thay đổi hoặc bị cắt; giữ nguyên tài khoản, cần đối soát trước khi replay."
                    )
            latest = max(r["date"] for rows in frames.values() for r in rows if r["date"] <= target)
            result = replay(orders, frames, account["initial_cash"], latest)
            for ticker, rows in frames.items():
                for bar in rows:
                    if bar["date"] <= latest:
                        db.execute(
                            "INSERT OR IGNORE INTO consumed_bars VALUES(?,?,?)",
                            (ticker, bar["date"], digest(bar)),
                        )
            for order_id, state in result["orders"].items():
                db.execute(
                    "UPDATE orders SET status=?,result=? WHERE id=?",
                    (state["state"], json.dumps(state), order_id),
                )
            db.execute(
                "UPDATE account SET clock=?,version=version+1,state=? WHERE id=1",
                (latest, json.dumps(result)),
            )
            db.execute(
                "INSERT INTO audit(time,event,details) VALUES(?,?,?)",
                (
                    _utc(),
                    "REPLAY_ADVANCED",
                    json.dumps(
                        {
                            "clock": latest,
                            "engine": "Backtrader",
                            "fills": sum(
                                1 for x in result["orders"].values() if x["filled_quantity"]
                            ),
                        }
                    ),
                ),
            )
        return self.snapshot()
