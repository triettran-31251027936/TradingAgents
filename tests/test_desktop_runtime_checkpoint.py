"""Resume must preserve the receipt gate and paid attempts across processes."""

import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from desktop.integration import artifacts
from desktop.runtime_models import ExecutablePortfolioDecision, Runtime, TokenUsage


def config(path, limit=1):
    return {
        "data_cache_dir": str(path),
        "desktop_usage": {"cache_enabled": False},
        "desktop_budget": {"enabled": True, "deepseek_max_requests": limit},
    }


def bind(path, *, resuming, limit=1):
    runtime, usage = Runtime(config(path, limit)), TokenUsage()
    saved = runtime.bind_checkpoint("exact-original-graph-thread", usage, resuming=resuming)
    return runtime, usage, saved


def decision():
    return ExecutablePortfolioDecision(
        rating="Buy",
        executive_summary="Synthetic test only",
        investment_thesis="Synthetic",
        execution={
            "action": "Buy",
            "quantity": 100,
            "limit_price": 20000,
            "rationale": "Synthetic",
            "data_gaps": [],
        },
    )


def worker(phase, path):
    """Two real Python processes share an actual LangGraph SQLite checkpoint."""
    import threading
    import time
    from typing import TypedDict

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict, total=False):
        partial: str
        remaining: str

    root = Path(path)
    runtime, usage, _ = bind(root, resuming=phase == "second")
    ready = threading.Event()

    def partial(state):
        assert phase == "first", "A completed branch was called again on resume"
        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="Synthetic partial argument",
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                )
            ],
            callbacks=[usage],
        )
        result = runtime.invoke(
            "Bear Researcher", {"provider": "deepseek", "model": "fake"}, model, "Evidence", {}
        )
        runtime._capture("Portfolio Manager", decision())
        runtime.handoffs = {"Bear Researcher": {"snapshot_fingerprint": None, "test_receipt": True}}
        runtime.persist()
        ready.set()
        return {"partial": result.content}

    def remaining(state):
        if phase == "first":
            assert ready.wait(5)
            time.sleep(0.05)
            raise RuntimeError("Intentional branch failure; no API")
        return {"remaining": "Recovered"}

    builder = StateGraph(State)
    builder.add_node("partial", partial)
    builder.add_node("remaining", remaining)
    for name in ("partial", "remaining"):
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    with SqliteSaver.from_conn_string(str(root / "native.sqlite")) as saver:
        graph = builder.compile(checkpointer=saver)
        args = {"configurable": {"thread_id": "same-native-thread"}}
        if phase == "first":
            with pytest.raises(RuntimeError, match="Intentional"):
                graph.invoke({}, args)
            return
        assert runtime.incomplete_roles == {"Bear Researcher": "output_token_limit"}
        assert runtime.decision == decision().model_dump(mode="json")
        assert runtime.handoffs["Bear Researcher"]["test_receipt"]
        assert runtime.stats["Bear Researcher"]["calls"] == 1
        assert usage.summary()["reported_input_tokens"] == 10
        result = graph.invoke(None, args)
        assert result["partial"].startswith("Synthetic partial")
        assert result["remaining"] == "Recovered"

    class Store:
        def proposal(self, *args):
            raise AssertionError("A restored incomplete argument must still block orders")

    quality = artifacts(root / "report", None, runtime, usage, Store(), "test-resume")
    assert quality["paper_proposal"]["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="giới hạn"):
        runtime.paid_budget.reserve("deepseek")
    (root / "resumed.json").write_text(json.dumps(quality))


def test_real_sqlite_resume_in_a_new_process_keeps_quality_gate_decision_and_budget(tmp_path):
    for phase in ("first", "second"):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), phase, str(tmp_path)],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "resumed.json").read_text())
    assert evidence["paid_budget"]["deepseek_attempts"] == 1
    assert evidence["report_quality"]["execution_blocking_roles"] == ["Bear Researcher"]


def test_failed_request_is_reserved_before_backend_and_remains_spent_on_resume(tmp_path):
    runtime, _, _ = bind(tmp_path, resuming=False)

    class Backend:
        def invoke(self, *args, **kwargs):
            raise ConnectionError("Synthetic failure")

    with pytest.raises(ConnectionError):
        runtime.invoke("Market Analyst", {"provider": "deepseek"}, Backend(), "Data", {})
    resumed, _, _ = bind(tmp_path, resuming=True)
    with pytest.raises(ValueError, match="giới hạn"):
        resumed.invoke("Market Analyst", {"provider": "deepseek"}, Backend(), "Data", {})
    assert resumed.paid_budget.calls == 1


def test_concurrent_writers_do_not_restore_an_older_paid_count(tmp_path):
    runtime, _, saved = bind(tmp_path, resuming=False, limit=20)

    class Backend:
        def invoke(self, *args, **kwargs):
            return AIMessage(content="Complete", response_metadata={"finish_reason": "stop"})

    def invoke(_):
        runtime.invoke("Market Analyst", {"provider": "deepseek"}, Backend(), "Data", {})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(invoke, range(20)))
    resumed, _, _ = bind(tmp_path, resuming=True, limit=20)
    assert resumed.paid_budget.calls == 20
    assert resumed.stats["Market Analyst"]["calls"] == 20
    assert stat.S_IMODE(saved.path.stat().st_mode) == 0o600


@pytest.mark.parametrize("damage", ["missing", "json", "fingerprint", "attempts"])
def test_missing_or_invalid_receipts_never_reset_the_paid_budget(tmp_path, damage):
    _, _, saved = bind(tmp_path, resuming=False)
    if damage == "missing":
        saved.path.unlink()
    elif damage == "json":
        saved.path.write_text("not-json")
    else:
        data = json.loads(saved.path.read_text())
        data["quant_fingerprint" if damage == "fingerprint" else "paid_attempts"] = "wrong"
        saved.path.write_text(json.dumps(data))
    before = saved.path.read_bytes() if saved.path.exists() else None
    with pytest.raises(ValueError, match="metadata desktop"):
        bind(tmp_path, resuming=True)
    assert (saved.path.read_bytes() if saved.path.exists() else None) == before


def test_callback_length_receipt_survives_crash_before_capture(tmp_path):
    _, usage, _ = bind(tmp_path, resuming=False)
    usage.on_chat_model_start({}, [], run_id="r", metadata={"ta_role": "News Analyst"})
    usage.on_llm_end(
        SimpleNamespace(
            generations=[
                [
                    SimpleNamespace(
                        message=AIMessage(
                            content="partial",
                            response_metadata={"finish_reason": "length"},
                        )
                    )
                ]
            ]
        ),
        run_id="r",
    )
    restored, _, _ = bind(tmp_path, resuming=True)
    assert restored.incomplete_roles == {"News Analyst": "output_token_limit"}


def test_fresh_run_does_not_inherit_metadata_of_a_completed_thread(tmp_path):
    runtime, _, _ = bind(tmp_path, resuming=False)
    runtime.incomplete_roles["Bear Researcher"] = "output_token_limit"
    runtime.persist()
    fresh, _, saved = bind(tmp_path, resuming=False)
    assert not fresh.incomplete_roles
    saved.clear()
    assert not saved.path.exists() and fresh._checkpoint is None


def test_inflight_usage_becomes_unknown_on_resume_without_key_or_endpoint_metadata(tmp_path):
    _, usage, saved = bind(tmp_path, resuming=False)
    usage.on_chat_model_start(
        {},
        [],
        run_id="inflight",
        metadata={
            "ta_role": "News Analyst",
            "ta_provider": "deepseek",
            "ta_requested_model": "fake",
            "private_endpoint": "https://secret.invalid/key",
            "api_key": "synthetic-secret",
        },
    )
    assert "synthetic-secret" not in saved.path.read_text()
    assert "secret.invalid" not in saved.path.read_text()
    _, restored, _ = bind(tmp_path, resuming=True)
    receipt = restored.summary()
    assert receipt["requests"] == receipt["unreported_requests"] == 1
    assert receipt["items"][0]["status"] == "interrupted_before_receipt"
    assert receipt["items"][0]["provider"] == "deepseek"
    _, again, _ = bind(tmp_path, resuming=True)
    assert again.summary()["requests"] == 1


if __name__ == "__main__":
    worker(sys.argv[1], sys.argv[2])


def test_cache_checkpoint_write_failure_never_falls_through_to_paid_call(tmp_path, monkeypatch):
    cfg = config(tmp_path, limit=2)
    cfg["desktop_usage"]["cache_enabled"] = True
    runtime = Runtime(cfg)
    model = SimpleNamespace(invoke=lambda *a, **kw: AIMessage(content="Cached report"))
    spec = {"provider": "deepseek", "model": "fake"}
    runtime.invoke("News Analyst", spec, model, "Same evidence", {})
    assert runtime.paid_budget.calls == 1

    def failed_write():
        raise ValueError("Synthetic checkpoint write failure")

    monkeypatch.setattr(runtime, "persist", failed_write)
    with pytest.raises(ValueError, match="checkpoint write failure"):
        runtime.invoke("News Analyst", spec, model, "Same evidence", {})
    assert runtime.paid_budget.calls == 1


def test_news_tool_evidence_survives_resume_and_omits_tool_text(tmp_path):
    from langchain_core.messages import SystemMessage, ToolMessage
    from desktop.prediction_markets import SEARCH

    cfg = config(tmp_path)
    day = "2026-11-10"
    quant = {"analysis_date": day, "fingerprint": "exact-snapshot", "metrics": {}, "ticker": "VHM.VN"}
    runtime = Runtime(cfg, quant=quant)
    runtime.bind_checkpoint("news", TokenUsage(), resuming=False)
    news = ToolMessage(
        name="get_global_news",
        tool_call_id="news-1",
        content=json.dumps({
            "upstream": "body of original article",
            "upstream_vendors": ["yfinance"],
            "vneconomy_rss": {"requested_dates": ["2026-11-03", day],
                             "status": "available", "records": [{"title": "excerpt"}]},
        }),
    )
    prediction = ToolMessage(
        name="get_prediction_markets",
        tool_call_id="pred-1",
        content=json.dumps({"asof": day, "status": "available", "source": SEARCH,
                            "markets": [{}], "replayed": True}),
    )
    model = SimpleNamespace(invoke=lambda *args, **kwargs: AIMessage(content="test"))
    context = SystemMessage(content="exact-snapshot")
    runtime.invoke("News Analyst", {"provider": "codex_cli"}, model, [context, news, prediction], {})
    runtime.invoke("News Analyst", {"provider": "codex_cli"}, model, [context, news], {})

    restored = Runtime(cfg, quant=quant)
    checkpoint = restored.bind_checkpoint("news", TokenUsage(), resuming=True)
    receipts = {item["tool"]: item for item in restored.news_tool_receipts.values()}
    assert set(receipts) == {"get_global_news", "get_prediction_markets"}
    assert receipts["get_global_news"]["upstream_vendors"] == ["yfinance"]
    assert receipts["get_global_news"]["item_count"] == 1
    assert receipts["get_prediction_markets"]["endpoint"] == SEARCH
    assert receipts["get_prediction_markets"]["replayed"]
    assert "body of original article" not in checkpoint.path.read_text()
