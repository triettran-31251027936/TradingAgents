"""Current issuer identity/business facts, separate from broker recommendations."""

from datetime import datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "template"):
            self.blocked += 1
        elif not self.blocked and tag in ("p", "div", "br", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "template"):
            self.blocked = max(0, self.blocked - 1)

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(value if isinstance(value, str) else "")
    return " ".join("".join(parser.parts).split())


def normalize(raw, ticker, asof, observed_at):
    observed = datetime.fromisoformat(observed_at) if isinstance(observed_at, str) else observed_at
    if observed.tzinfo is None:
        raise ValueError("Issuer observation requires timezone")
    base = {
        "observed_at": observed.isoformat(),
        "source": "Vietcap company/details",
        "historical_archive": False,
    }
    if observed.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat() != asof:
        return {
            **base,
            "status": "withheld",
            "reason": "Current company profile is not a historical point-in-time archive",
        }
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or data.get("ticker") != ticker:
        return {**base, "status": "unavailable", "reason": "Missing or mismatched issuer identity"}
    # Explicit allowlist: broker BUY/SELL rating, target and projections are not facts.
    return {
        **base,
        "status": "available",
        "ticker": ticker + ".VN",
        "sector": data.get("sector"),
        "sector_vi": data.get("sectorVn"),
        "company_type": data.get("comTypeCode"),
        "description_vi": plain(data.get("profile")),
        "description_en": plain(data.get("enProfile")),
        "listing_date": data.get("listingDate"),
        "market": {
            key: data.get(key)
            for key in ("currentPrice", "marketCap", "numberOfSharesMktCap", "issueShare")
        },
    }
