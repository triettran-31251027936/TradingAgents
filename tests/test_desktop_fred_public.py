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


def next_day(monkeypatch):
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return RealDatetime(2026, 11, 5, 0, 30, tzinfo=public.UTC).astimezone(tz)

    monkeypatch.setattr(public, "datetime", Clock)


def test_historical_replay_uses_only_same_day_observed_snapshot(live, monkeypatch):
    root, calls = live
    public.get_macro_data("cpi", "2026-11-04", 365, root)
    path = next((root / "fred-public").glob("*.json"))
    original = path.read_bytes()
    next_day(monkeypatch)
    text = public.get_macro_data("cpi", "2026-11-04", 365, root)
    assert len(calls) == 2 and path.read_bytes() == original
    assert "336.298 (2026-09-01)" in text
    assert "locally observed on the analysis date" in text
    assert "Not an ALFRED revision archive" in text
    assert "intraday availability before observed_at" in text
    assert "2026-11-04T00:30:00+00:00" in text


@pytest.mark.parametrize(
    "change",
    [
        "future_observation",
        "unaware_observation",
        "future_source_update",
        "wrong_rows",
        "raw_hash",
        "wrong_source",
        "wrong_window",
        "checksum",
    ],
)
def test_invalid_local_vintage_is_withheld_without_download(live, monkeypatch, change):
    root, calls = live
    public.get_macro_data("cpi", "2026-11-04", 365, root)
    path = next((root / "fred-public").glob("*.json"))
    data = json.loads(path.read_text())
    if change == "future_observation":
        data["observed_at"] = "2026-11-05T00:30:00+00:00"
    elif change == "unaware_observation":
        data["observed_at"] = "2026-11-04T07:30:00"
    elif change == "future_source_update":
        data["source_last_updated"] = "2026-11-05T08:00:00+00:00"
    elif change == "wrong_rows":
        data["observations"][0]["value"] = "999999"
    elif change == "raw_hash":
        data["sources"][1]["sha256"] = "0" * 64
    elif change == "wrong_source":
        data["sources"][1]["params"]["id"] = "UNRATE"
    elif change == "wrong_window":
        data["window_start"] = "2020-01-01"
    data.pop("content_sha256")
    data["content_sha256"] = "bad" if change == "checksum" else vietnam.digest(data)
    path.write_text(json.dumps(data))
    next_day(monkeypatch)
    text = public.get_macro_data("cpi", "2026-11-04", 365, root)
    assert text.startswith("DATA_UNAVAILABLE:") and len(calls) == 2


def test_local_vintage_symlink_and_different_lookback_are_not_used(live, monkeypatch):
    root, calls = live
    public.get_macro_data("cpi", "2026-11-04", 365, root)
    path = next((root / "fred-public").glob("*.json"))
    saved = root / "preserved.json"
    path.rename(saved)
    path.symlink_to(saved)
    next_day(monkeypatch)
    assert public.get_macro_data("cpi", "2026-11-04", 365, root).startswith("DATA_UNAVAILABLE:")
    assert public.get_macro_data("cpi", "2026-11-04", 90, root).startswith("DATA_UNAVAILABLE:")
    assert len(calls) == 2


def test_historical_original_router_replays_without_network(live, monkeypatch):
    root, calls = live
    public.get_macro_data("cpi", "2026-11-04", 365, root)
    next_day(monkeypatch)
    with (
        vietnam.vendor_scope({"ticker": "VHM.VN", "analysis_date": "2026-11-04"}, root),
        run_config({"tool_vendors": {"get_macro_indicators": "vietnam"}}),
    ):
        text = router.route_to_vendor("get_macro_indicators", "cpi", "2026-11-04", 365)
    assert "locally observed on the analysis date" in text and len(calls) == 2
