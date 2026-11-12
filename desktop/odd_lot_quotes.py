"""Observe HOSE odd-lot top of book from TVSI's public board, without trading."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import time

from desktop.market_board import phase
from desktop.vietnam import TZ, symbol

SOURCE = "https://prs.tvsi.com.vn/banggia/hsx/odd"
WS_URL = "wss://streamprs.tvsi.com.vn:5000/ws"
BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
MAX_DECODED = 4_000_000


def decode(frame):
    """Decode the LZ-String Base64 wire format used by the public board."""
    if not isinstance(frame, str) or not frame or len(frame) > 1_000_000:
        raise ValueError("Invalid TVSI frame")
    codes = (BASE64.index(char) for char in frame)
    bits = ((code >> bit) & 1 for code in codes for bit in range(5, -1, -1))

    def read(width):
        value = 0
        for bit in range(width):
            value |= next(bits) << bit
        return value

    try:
        start = read(2)
        if start not in (0, 1):
            raise ValueError("Empty TVSI message")
        previous = chr(read(8 if start == 0 else 16))
        dictionary = ["", "", "", previous]
        width, remaining = 3, 4
        result, size = [previous], 1
        while size <= MAX_DECODED:
            code = read(width)
            if code in (0, 1):
                dictionary.append(chr(read(8 if code == 0 else 16)))
                code = len(dictionary) - 1
                remaining -= 1
            elif code == 2:
                # The wire format encodes UTF-16 code units.
                raw = "".join(result).encode("utf-16-le", "surrogatepass").decode("utf-16-le")
                return json.loads(raw)
            if remaining == 0:
                remaining = 1 << width
                width += 1
            if code < len(dictionary):
                entry = dictionary[code]
            elif code == len(dictionary):
                entry = previous + previous[0]
            else:
                raise ValueError("Invalid TVSI dictionary code")
            if not entry:
                raise ValueError("Invalid TVSI dictionary entry")
            result.append(entry)
            size += len(entry)
            dictionary.append(previous + entry[0])
            remaining -= 1
            previous = entry
            if remaining == 0:
                remaining = 1 << width
                width += 1
    except (StopIteration, UnicodeError) as exc:
        raise ValueError("Incomplete TVSI frame") from exc
    raise ValueError("TVSI decoded message exceeds limit")


def board_row(message, ticker):
    matches = [row for row in message.split("$") if row.startswith(ticker + "|")]
    if len(matches) != 1:
        raise ValueError("No unique TVSI issuer row")
    fields = dict(prop.split("*", 2)[:2] for prop in matches[0].split("|")[1:] if "*" in prop)
    if fields.get("47") != "HSX":
        raise ValueError("TVSI issuer is not on HOSE")

    def integer(index, multiplier=1):
        try:
            value = Decimal(fields.get(index, "")) * multiplier
        except InvalidOperation:
            return None
        return int(value) if value.is_finite() and 0 < value < 10**12 and value == int(value) else None

    return {
        "best_bid_vnd": integer("76", 1000), "best_bid_shares": integer("77"),
        "best_ask_vnd": integer("82", 1000), "best_ask_shares": integer("83"),
        "last_match_vnd": integer("66", 1000), "last_match_shares": integer("69"),
        "cumulative_shares": integer("70"),
    }


def observe(ticker, asof):
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    name = symbol(ticker)
    unavailable = {"status": "unavailable", "source": SOURCE, "ticker": name + ".VN"}
    now = datetime.now(TZ)
    if now.date().isoformat() != asof or phase(now, "HOSE") != "continuous":
        return {**unavailable, "reason": "No same-day continuous HOSE session"}
    try:
        with connect(WS_URL, origin="https://prs.tvsi.com.vn", open_timeout=4, close_timeout=1,
                     max_size=1_000_000) as ws:
            for kind in (4, 2, 3, 5, 6):
                ws.send(json.dumps({"msgType": kind}))
            end = time.monotonic() + 6
            stamp = row = observed = None
            while (left := end - time.monotonic()) > 0:
                frame = decode(ws.recv(timeout=left))
                if frame.get("msgType") == 1:
                    stamp = datetime.fromtimestamp(int(frame["message"]) / 1000, TZ)
                if frame.get("msgType") == 4:
                    row = board_row(frame["message"], name)
                    observed = datetime.now(TZ)
                if row is not None and stamp is not None:
                    break
        if row is None or stamp is None or abs((observed - stamp).total_seconds()) > 5:
            raise ValueError("No timely server clock adjacent to the board snapshot")
        if phase(observed, "HOSE") != "continuous":
            raise ValueError("HOSE continuous session has ended")
        return {
            **unavailable, **row, "status": "observed",
            "observed_at": observed.isoformat(), "board_server_clock": stamp.isoformat(),
            "scope": "Odd-lot board snapshot. Observation/server time is not an exchange trade timestamp. "
                     "Displayed quantity is indicative; odd-lot execution is not enabled.",
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, WebSocketException) as exc:
        return {**unavailable, "reason": type(exc).__name__}
