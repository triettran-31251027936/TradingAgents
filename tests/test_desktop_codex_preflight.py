"""Quota decisions, read-only RPC, concurrency and no-paid-call guarantees."""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from desktop import codex_preflight as p
from desktop.runtime_models import Runtime


def snapshot(used=20, **extra):
    return {
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": 2000},
            **extra,
        }
    }


def test_codex_bucket_preferred_and_unrelated_account_details_not_exported():
    data = snapshot(100)
    data.update(
        accountId="private-account",
        token="private-token",
        rateLimitsByLimitId={"codex": snapshot(20)["rateLimits"]},
    )
    result = p.summarize(data, now=1000)
    assert (
        result["status"] == "available" and result["windows"]["primary"]["remaining_percent"] == 80
    )
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {"usedPercent": None},
        {"usedPercent": True},
        {"usedPercent": float("nan")},
        {"usedPercent": 101},
        {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 900},
    ],
)
def test_unknown_or_expired_window_is_not_zero_usage_or_exhausted(row):
    data = snapshot()
    data["rateLimits"]["primary"] = row
    result = p.summarize(data, now=1000)
    assert result["status"] == "unavailable" and not result["windows"]


def test_exhaustion_and_credit_capacity_do_not_spend_or_redeem_anything():
    assert p.summarize(snapshot(100), now=1000)["status"] == "exhausted"
    assert (
        p.summarize(snapshot(100, credits={"hasCredits": True, "balance": "0.20"}), now=1000)[
            "status"
        ]
        == "available"
    )
    assert (
        p.summarize(snapshot(100, credits={"hasCredits": True, "balance": "0"}), now=1000)["status"]
        == "exhausted"
    )
    assert (
        p.summarize(snapshot(100, credits={"unlimited": True}), now=1000)["status"] == "available"
    )
    assert (
        p.summarize({**snapshot(), "ordinaryUsageAllowed": False}, now=1000)["status"]
        == "exhausted"
    )
    assert (
        p.summarize({**snapshot(100), "ordinaryUsageAllowed": True}, now=1000)["status"]
        == "available"
    )
    assert (
        p.summarize({**snapshot(), "spendControlReached": True}, now=1000)["status"] == "exhausted"
    )


def fake_cli(tmp_path, mode="normal"):
    path = tmp_path / "fake-codex"
    log = tmp_path / "methods.jsonl"
    path.write_text(
        f"#!{sys.executable}\n"
        + f"LOG={str(log)!r}\nMODE={mode!r}\n"
        + """
import json,sys,time,os
if MODE == "timeout":
    open(LOG,"w").write(str(os.getpid()))
    time.sleep(30)
for line in sys.stdin:
    msg=json.loads(line)
    with open(LOG,"a") as f:f.write(msg["method"]+"\\n")
    if msg["method"]=="initialize":
        print(json.dumps({"id":1,"result":{}}),flush=True)
    elif msg["method"]=="account/rateLimits/read":
        result={"rateLimits":{"limitId":"codex","primary":{"usedPercent":20,"windowDurationMins":300,"resetsAt":int(time.time())+1000}}}
        reply={"id":2,"result":result} if MODE=="normal" else {"id":2,"error":{"message":"secret-token-do-not-display"}}
        print(json.dumps(reply),flush=True)
"""
    )
    path.chmod(0o700)
    return path, log


def test_rpc_only_initializes_and_reads_no_thread_turn_login_or_credit_action(tmp_path):
    path, log = fake_cli(tmp_path)
    result = p.rpc_snapshot(str(path), timeout=2)
    assert result["status"] == "available"
    assert log.read_text().splitlines() == ["initialize", "initialized", "account/rateLimits/read"]


def test_rpc_error_never_echoes_account_or_auth_data(tmp_path):
    path, _ = fake_cli(tmp_path, "error")
    result = p.rpc_snapshot(str(path), timeout=2)
    assert result["reason"] == "quota_read_failed" and "secret" not in json.dumps(result)


def test_rpc_timeout_terminates_and_reaps_child(tmp_path, monkeypatch):
    path, _ = fake_cli(tmp_path, "timeout")
    children = []
    original = p.subprocess.Popen

    def capture(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(p.subprocess, "Popen", capture)
    start = time.monotonic()
    result = p.rpc_snapshot(str(path), timeout=0.2)
    assert result["reason"] == "timeout" and time.monotonic() - start < 2
    assert len(children) == 1 and children[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


def config(tmp_path):
    return {
        "data_cache_dir": str(tmp_path),
        "llm_provider": "codex_cli",
        "desktop_budget": {"enabled": True, "deepseek_max_requests": 18},
        "desktop_role_models": {
            "News Analyst": {"provider": "deepseek", "model": "deepseek-flash"}
        },
    }


def test_exhausted_codex_prevents_first_deepseek_reservation_and_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "read_limits", lambda: p.summarize(snapshot(100), now=1000))
    runtime = Runtime(config(tmp_path), preflight=True)

    class Backend:
        def invoke(self, *args, **kwargs):
            pytest.fail("No model call may occur after exhausted quota")

    with pytest.raises(ValueError, match="hạn mức"):
        runtime.invoke(
            "News Analyst", {"provider": "deepseek"}, Backend(), [HumanMessage(content="new")], {}
        )
    assert runtime.paid_budget.calls == 0 and runtime.stats == {}


def test_parallel_first_calls_share_one_read_only_probe(tmp_path, monkeypatch):
    calls = []

    def read():
        calls.append(True)
        time.sleep(0.02)
        return p.summarize(snapshot(), now=1000)

    monkeypatch.setattr(p, "read_limits", read)
    guard = p.Preflight(config(tmp_path), lambda *a: None)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: guard.check(), range(8)))
    assert len(calls) == 1


def test_exact_cache_hit_works_without_new_quota_probe(tmp_path, monkeypatch):
    calls = []

    def read():
        calls.append(True)
        return p.summarize(snapshot(), now=1000)

    monkeypatch.setattr(p, "read_limits", read)

    class Backend:
        def invoke(self, *a, **k):
            return AIMessage(content="cached report")

    cfg = config(tmp_path)
    args = ("News Analyst", {"provider": "deepseek"}, Backend(), [HumanMessage(content="same")], {})
    Runtime(cfg, preflight=True).invoke(*args)
    assert len(calls) == 1
    monkeypatch.setattr(
        p, "read_limits", lambda: pytest.fail("Cached requests must not probe quota")
    )
    second = Runtime(cfg, preflight=True)
    assert second.invoke(*args).content == "cached report" and second.paid_budget.calls == 0


def test_non_codex_routes_do_not_probe_an_unrelated_account(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg["llm_provider"] = "deepseek"
    monkeypatch.setattr(p, "read_limits", lambda: pytest.fail("No Codex selected"))
    p.Preflight(cfg, lambda *a: None).check()


def test_minimal_embedding_config_has_no_implicit_codex_account_probe(monkeypatch):
    monkeypatch.setattr(p, "read_limits", lambda: pytest.fail("No provider selected"))
    guard = p.Preflight({}, lambda *a: None)
    guard.check()
    assert not guard.required and guard.receipt is None
