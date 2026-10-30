"""Actual SDMX semantics, observation vintages, units, offline reuse and routing."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests

from desktop import macro_vietnam as m

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 30, 12, tzinfo=TZ)


def xml(kind="cpi", mult="3", duplicate=False, prepared="2026-07-04T00:00:00Z"):
    if kind == "cpi":
        series = '<Series REF_AREA="VN" INDICATOR="PCPI_IX" FREQ="M" BASE_PER="2019" UNIT_MULT="0"><Obs TIME_PERIOD="2025-08" OBS_VALUE="110"/><Obs TIME_PERIOD="2025-09" OBS_VALUE="111"/><Obs TIME_PERIOD="2026-08" OBS_VALUE="115.5"/><Obs TIME_PERIOD="2026-09" OBS_VALUE="116.55"/><Obs TIME_PERIOD="2026-11" OBS_VALUE="999"/></Series>'
    else:
        series = "".join(
            f'<Series REF_AREA="VN" INDICATOR="{code}" FREQ="Q" UNIT_MULT="{mult}"><Obs TIME_PERIOD="2026-Q1" OBS_VALUE="{v}"/><Obs TIME_PERIOD="2026-Q2" OBS_VALUE="{v}"/></Series>'
            for code, v in [
                ("LLF_PE_NUM", "53787.5"),
                ("LE_PE_NUM", "52681.972"),
                ("LUR_PT", "2.23189"),
                ("LUR15O_PT", "8.67088"),
                ("LEU_PT", "1.62046"),
            ]
        )
    return (
        "<StructureSpecificData><Header><Prepared>"
        + prepared
        + "</Prepared></Header><DataSet>"
        + series
        + (series if duplicate else "")
        + "</DataSet></StructureSpecificData>"
    ).encode()


class Response:
    def __init__(self, raw):
        self.raw = raw

    def raise_for_status(self):
        pass

    def iter_content(self, _):
        yield self.raw

    def close(self):
        pass


class Session:
    def __init__(self, error=False):
        self.headers = {}
        self.calls = 0
        self.error = error

    def get(self, url, **kwargs):
        self.calls += 1
        if self.error:
            raise requests.ConnectionError("test offline")
        return Response(xml("cpi" if "CPI" in url else "labor"))


def test_scale_counts_and_keep_percentage_not_1000_times_larger(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    result = m.load("2026-10-30", tmp_path, now=NOW, session=Session())
    metrics = m.metrics(result)
    assert metrics["vn_labor_force"]["value"] == 53787500
    assert metrics["vn_unemployment"]["value"] == pytest.approx(0.0223189)
    assert metrics["vn_underemployment"]["value"] == pytest.approx(0.0162046)
    assert metrics["vn_cpi_yoy"]["value"] == pytest.approx(0.05)
    assert metrics["vn_cpi_yoy"]["unit"] == "fraction"
    assert result["series"]["vn_unemployment"]["dimensions"]["UNIT_MULT"] == "3"
    assert result["series"]["vn_unemployment"]["unit_multiplier_applied"] == 1
    assert result["series"]["vn_unemployment"]["warnings"]
    assert result["series"]["vn_cpi"]["latest"]["period"] == "2026-09"


def test_stale_period_is_not_presented_as_current(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    result = m.load("2026-10-30", tmp_path, now=NOW, session=Session())
    labor = result["series"]["vn_unemployment"]
    assert labor["status"] == "stale" and labor["age_days"] == 122
    assert "stale" in " ".join(result["warnings"])
    assert m.metrics(result)["vn_unemployment"]["asof"] == "2026-06-30"


def test_no_unobserved_historical_macro_leaks_or_requests(tmp_path):
    session = Session()
    result = m.load("2026-10-29", tmp_path, now=NOW, session=session)
    assert session.calls == 0
    assert all(row["value"] is None for row in m.metrics(result).values())
    assert result["series"]["vn_cpi"]["status"] == "withheld"


def test_archive_reuses_only_vintage_observed_by_analysis_date(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    old = m.load("2026-10-29", tmp_path, now=NOW - timedelta(days=1), session=Session())
    session = Session(error=True)
    historical = m.load("2026-10-29", tmp_path, now=NOW, session=session)
    assert session.calls == 0
    assert historical["series"]["vn_cpi"]["latest"] == old["series"]["vn_cpi"]["latest"]
    assert historical["series"]["vn_cpi"]["first_observed_at"].startswith("2026-10-29")


def test_six_hour_cache_and_error_preserve_original_retrieval_time(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    first = m.load("2026-10-30", tmp_path, now=NOW, session=Session())
    session = Session(error=True)
    m.load("2026-10-30", tmp_path, now=NOW + timedelta(minutes=10), session=session)
    assert session.calls == 0
    failed = m.load("2026-10-30", tmp_path, now=NOW + timedelta(hours=7), session=session)
    assert session.calls == 2
    assert failed["series"]["vn_cpi"]["retrieved_at"] == first["series"]["vn_cpi"]["retrieved_at"]
    assert failed["series"]["vn_cpi"]["source_connection_status"] == "cached_source_error"


def test_offline_no_cache_returns_missing_not_fake_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    result = m.load("2026-10-30", tmp_path, now=NOW, session=Session(error=True))
    assert all(row["value"] is None for row in m.metrics(result).values())
    assert result["warnings"]


@pytest.mark.parametrize(
    "raw",
    [
        b'<!DOCTYPE a [<!ENTITY x "bad">]><StructureSpecificData/>',
        b"<html/>",
        xml(duplicate=True),
        xml(prepared="2026-11-01T00:00:00Z"),
    ],
)
def test_invalid_ambiguous_or_future_sdmx_rejected(raw):
    with pytest.raises(ValueError):
        m.parse_xml(raw, NOW.date(), NOW)


def test_annual_and_quarter_month_ends_are_not_mixed():
    assert str(m.period_end("2024-02", "M")) == "2024-02-29"
    assert str(m.period_end("2026-Q2", "Q")) == "2026-06-30"
    assert str(m.period_end("2026", "A")) == "2026-12-31"
    with pytest.raises(ValueError):
        m.period_end("2026-03", "Q")


def test_unknown_population_multiplier_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    result = m.parse_xml(xml("labor", mult="9"), NOW.date(), NOW)
    source = {
        "url": "https://test.invalid",
        "status": "available",
        "sha256": "test",
        "first_observed_at": NOW.isoformat(),
        "retrieved_at": NOW.isoformat(),
    }
    with pytest.raises(ValueError):
        m.normalize("vn_labor_force", result["LLF_PE_NUM"], source, NOW.date())


def test_original_macro_tool_serves_domestic_and_preserves_fred(tmp_path, monkeypatch):
    from desktop import vietnam
    from tradingagents.dataflows import router

    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    macro = m.load("2026-10-30", tmp_path, now=NOW, session=Session())
    prior = router.VENDOR_METHODS["get_macro_indicators"].get("vietnam")
    monkeypatch.setitem(
        router.VENDOR_METHODS["get_macro_indicators"],
        "fred",
        lambda indicator, *args: "US:" + indicator,
    )
    with vietnam.vendor_scope(
        {"ticker": "HPG.VN", "analysis_date": "2026-10-30", "domestic_macro": macro}
    ):
        tool = router.VENDOR_METHODS["get_macro_indicators"]["vietnam"]
        result = json.loads(tool("vn_unemployment", "2026-10-30"))
        assert result["latest"]["value"] == 2.23189 and result["unit"] == "percent"
        assert tool("cpi", "2026-10-30") == "US:cpi"
        raw = json.loads(tool("vn_nsdp:LLF_PE_NUM", "2026-10-30"))
        assert raw["dimensions"]["UNIT_MULT"] == "3"
        assert raw["observations"][-1]["raw_value"] == 53787.5
        with pytest.raises(ValueError):
            tool("vn_cpi", "2026-10-29")
    assert router.VENDOR_METHODS["get_macro_indicators"].get("vietnam") is prior


def test_tool_window_and_summary_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    result = m.load("2026-10-30", tmp_path, now=NOW, session=Session())
    short = json.loads(m.tool(result, "vn_cpi", "2026-10-30", 10))
    assert short["observations"] == [] and short["latest"]["period"] == "2026-09"
    summarized = m.summary(result)
    assert "observations" not in summarized["series"]["vn_cpi"]
    assert "raw_series" not in summarized
    assert json.loads(m.tool(result, "vn_unknown", "2026-10-30"))["status"] == "unsupported"
    with pytest.raises(ValueError):
        m.tool(result, "vn_cpi", "2026-10-30", True)
