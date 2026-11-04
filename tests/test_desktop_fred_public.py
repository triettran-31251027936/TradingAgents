"""Keyless live FRED keeps metadata, units, vintage boundaries and original API."""

import json
from datetime import datetime as RealDatetime

import pytest

from desktop import fred_public as public, vietnam
from tradingagents.dataflows import router
from tradingagents.dataflows.config import run_config

META = b"""<script type="application/ld+json">{"@type":"Dataset","alternateName":"CPIAUCSL","name":"US CPI index","dateModified":"2026-10-14T08:08:00-05:00"}</script><div class="meta-col">Units: <span class="series-meta-value-units">Index 1982-1984=100</span>, Seasonally Adjusted</div><span class="series-meta-value-frequency">Monthly</span>"""
CSV = b"observation_date,CPIAUCSL\n2026-08-01,334.131\n2026-09-01,336.298\n2026-10-01,.\n"


@pytest.fixture
def live(tmp_path, monkeypatch):
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return RealDatetime(2026, 11, 4, 0, 30, tzinfo=public.UTC).astimezone(tz)

    monkeypatch.setattr(public, "datetime", Clock)
    monkeypatch.setattr(public.fred, "_fred_today", lambda: "2026-11-03")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    calls = []

    def download(url, params):
        calls.append((url, params))
        return META if url.startswith(public.META_URL) else CSV

    monkeypatch.setattr(public, "download", download)
    return tmp_path, calls


def test_live_data_retains_us_units_and_revision_scope_and_uses_cache(live):
    root, calls = live
    text = public.get_macro_data("cpi", "2026-11-04", 365, root)
    assert len(calls) == 2 and calls[1][1]["coed"] == "2026-11-03"
    assert "336.298 (2026-09-01)" in text and "Index 1982-1984=100" in text
    assert "not an ALFRED historical vintage" in text and "not Vietnamese substitutes" in text
    assert "2026-10-01 |" not in text
    assert "level change" in text and "inflation rate" in text
    assert "Seasonally Adjusted" in text
    assert public.get_macro_data("CPIAUCSL", "2026-11-04", 365, root) == text
    assert len(calls) == 2
    bundle = json.loads(next((root / "fred-public").glob("*.json")).read_text())
    checksum = bundle.pop("content_sha256")
    assert checksum == vietnam.digest(bundle)
    assert len(bundle["sources"]) == 2 and len(bundle["observations"]) == 2


def test_historical_vintage_is_not_replaced_by_latest_csv(live):
    root, calls = live
    result = public.get_macro_data("cpi", "2026-10-23", 365, root)
    assert result.startswith("DATA_UNAVAILABLE:") and "not requested or backdated" in result
    assert calls == [] and not (root / "fred-public").exists()


def test_keyed_fred_preserves_original_transport_and_historical_pin(live, monkeypatch):
    root, calls = live
    monkeypatch.setenv("FRED_API_KEY", "synthetic-private-key")
    received = []
    monkeypatch.setattr(
        public.fred, "get_macro_data", lambda *a: received.append(a) or "Original API"
    )
    assert public.get_macro_data("cpi", "2026-10-23", 365, root) == "Original API"
    assert received == [("cpi", "2026-10-23", 365)] and calls == []


@pytest.mark.parametrize(
    "raw",
    [
        b"observation_date,UNRATE\n2026-09-01,336.298\n",
        b"observation_date,CPIAUCSL\n2026-09-01,nan\n",
        b"observation_date,CPIAUCSL\n2026-09-01,1\n2026-09-01,2\n",
    ],
)
def test_wrong_series_and_malformed_values_are_not_used(raw):
    with pytest.raises(ValueError):
        public.observations(raw, "CPIAUCSL", "2026-01-01", "2026-11-03")


def test_missing_metadata_identity_or_units_is_not_invented():
    with pytest.raises(ValueError):
        public.metadata(META.replace(b"CPIAUCSL", b"UNRATE"), "CPIAUCSL")
    with pytest.raises(ValueError):
        public.metadata(b"<html>Access denied</html>", "CPIAUCSL")


@pytest.mark.parametrize("indicator,days", [("../CPI", 365), ("cpi", 0), ("cpi", True)])
def test_bad_arguments_do_not_send_requests(live, indicator, days):
    root, calls = live
    with pytest.raises(ValueError):
        public.get_macro_data(indicator, "2026-11-04", days, root)
    assert calls == []


def test_vn_job_routes_original_macro_tool_to_live_public_fred(live):
    root, calls = live
    bundle = {"ticker": "VHM.VN", "analysis_date": "2026-11-04"}
    original = router.VENDOR_METHODS["get_macro_indicators"].copy()
    with (
        vietnam.vendor_scope(bundle, root),
        run_config({"tool_vendors": {"get_macro_indicators": "vietnam"}}),
    ):
        text = router.route_to_vendor("get_macro_indicators", "cpi", "2026-11-04", 365)
    assert "336.298" in text and len(calls) == 2
    assert router.VENDOR_METHODS["get_macro_indicators"] == original
