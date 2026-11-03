"""A truncated reflection must not become a resolved lesson for future agents."""

import pytest
from langchain_core.messages import AIMessage

from desktop.runtime_models import Runtime
from tradingagents.memory import TradingMemoryLog, settlement
from tradingagents.memory.reflection import Reflector


def test_truncated_reflection_stays_pending_then_complete_response_can_settle(tmp_path, monkeypatch):
    cfg = {"data_cache_dir": str(tmp_path / "cache"), "memory_log_path": str(tmp_path / "memory.md"),
           "desktop_budget": {"enabled": True, "deepseek_max_requests": 2},
           "desktop_usage": {"cache_enabled": False}, "holding_period_days": 5,
           "benchmark_map": {".VN": "VNINDEX.VN"}}
    log = TradingMemoryLog(cfg)
    log.store_decision("BSR.VN", "2026-10-23", "Rating: Hold\nSynthetic test only; no direction claimed.", rating="Hold")
    runtime = Runtime(cfg)

    class Backend:
        finish = "length"

        def invoke(self, *args, **kwargs):
            return AIMessage(content="Synthetic reflection", response_metadata={"finish_reason": self.finish})

    backend = Backend()

    class Routed:
        def invoke(self, value):
            return runtime.invoke("Memory Log", {"provider": "deepseek"}, backend, value, {})

    monkeypatch.setattr(settlement, "fetch_returns", lambda *a, **kw: (-.05, -.02, 5, "2026-11-02"))
    reflector = Reflector(Routed())
    first = settlement.settle_pending("BSR.VN", log, reflector, cfg)
    assert not first.settled and len(first.failed) == 1
    assert log.load_entries()[0]["pending"]
    assert not log.get_past_context("BSR.VN", as_of="2026-11-03")
    assert runtime.incomplete_roles == {"Memory Log": "output_token_limit"}
    backend.finish = "stop"
    second = settlement.settle_pending("BSR.VN", log, reflector, cfg)
    assert second.settled == [("BSR.VN", "2026-10-23")] and not second.failed
    assert not log.load_entries()[0]["pending"]
    assert not log.get_past_context("BSR.VN", as_of="2026-11-01")
    assert log.get_past_context("BSR.VN", as_of="2026-11-02")
    assert not runtime.incomplete_roles and runtime.paid_budget.calls == 2


def test_legacy_cached_partial_memory_never_triggers_paid_fallback(tmp_path):
    # A valid message cache containing a length stop must fail the current gate,
    # not be mistaken for malformed JSON and retried on the paid backend.
    from desktop.vietnam import write_json

    cfg = {"data_cache_dir": str(tmp_path)}
    runtime = Runtime(cfg)
    role = "Memory Log"

    class Backend:
        calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            return AIMessage(content="Complete", response_metadata={"finish_reason": "stop"})

    backend = Backend()
    spec = {"provider": "deepseek", "model": "fake"}
    runtime.invoke(role, spec, backend, "Same input", {})
    path = next((tmp_path / "llm-requests").glob("*.json"))
    import json

    record = json.loads(path.read_text())
    record["value"]["response_metadata"]["finish_reason"] = "length"
    write_json(path, record)
    resumed = Runtime(cfg)
    with pytest.raises(ValueError, match="pending"):
        resumed.invoke(role, spec, backend, "Same input", {})
    assert backend.calls == 1 and resumed.paid_budget.calls == 0
