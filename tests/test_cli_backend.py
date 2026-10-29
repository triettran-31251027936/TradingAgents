"""CLI transport, tool loop and structured decisions, without paid inference."""

import json
import sys

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from tradingagents.llm_clients.cli_client import CLIChatModel, _strict_schema
from tradingagents.llm_clients.factory import create_llm_client, create_tier_client


@pytest.fixture
def cli_model(tmp_path, monkeypatch):
    executable = tmp_path / "fake-cli"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys, time
args = sys.argv[1:]
prompt = next((a.split("=", 1)[1] for a in args if a.startswith("--print=")), None)
if prompt is None:
    prompt = sys.stdin.read()
pathlib.Path(os.environ["TEST_CAPTURE"]).write_text(prompt)
time.sleep(float(os.environ.get("TEST_DELAY", "0")))
if os.environ.get("TEST_ERROR_MESSAGE"):
    print(os.environ["TEST_ERROR_MESSAGE"], file=sys.stderr)
sys.exit(int(os.environ["TEST_EXIT"])) if os.environ.get("TEST_EXIT") else None
answer = json.loads(os.environ.get("TEST_ANSWER", '{"content":"report","tool_calls":[]}'))
if "exec" in args:
    schema = pathlib.Path(args[args.index("--output-schema") + 1])
    json.loads(schema.read_text())
    pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(answer))
    events = [{"type":"turn.completed"}]
else:
    events = [{"event":"result","result":{"status":"SUCCESS","structured_output":answer}}]
if os.environ.get("TEST_NATIVE_TOOL"):
    events.insert(0, {"type":"item.completed","item":{"type":"command_execution"},
        "event":"step_update","step_update":{"step_type":"tool"}})
if os.environ.get("TEST_FINISH"):
    events.insert(0, {"event":"step_update","step_update":{"step_type":"tool","tool_name":"finish"}})
if os.environ.get("TEST_NO_COMPLETION"):
    events = []
for event in events:
    print(json.dumps(event))
''', encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("TEST_CAPTURE", str(tmp_path / "prompt.txt"))
    return CLIChatModel(provider="codex_cli", executable=str(executable),
                        runtime_dir=str(tmp_path / "runtime"), timeout=5)


@tool
def quote(symbol: str) -> str:
    """Get a stock quote."""
    return f"{symbol}: 100"


@pytest.mark.parametrize("provider", ["codex_cli", "agy_cli"])
def test_real_subprocess_io_and_tool_result_roundtrip(cli_model, monkeypatch, tmp_path, provider):
    cli_model = cli_model.model_copy(update={"provider": provider})
    monkeypatch.setenv("TEST_ANSWER", json.dumps({"content": "", "tool_calls": [
        {"name": "quote", "arguments": '{"symbol":"FPT"}'}]}))
    bound = cli_model.bind_tools([quote])
    first = bound.invoke([HumanMessage(content="Analyze FPT")])
    call = first.tool_calls[0]
    assert call["name"] == "quote" and call["args"] == {"symbol": "FPT"}
    result = quote.invoke(call["args"])
    monkeypatch.setenv("TEST_ANSWER", '{"content":"FPT quote is 100","tool_calls":[]}')
    final = bound.invoke([HumanMessage(content="Analyze FPT"), first,
                          ToolMessage(content=result, name="quote", tool_call_id=call["id"])])
    assert final.content == "FPT quote is 100" and not final.tool_calls
    sent = (tmp_path / "prompt.txt").read_text()
    assert "FPT: 100" in sent and call["id"] in sent


class Decision(BaseModel):
    rating: str
    price: float | None = None


@pytest.mark.parametrize("provider", ["codex_cli", "agy_cli"])
def test_structured_pydantic_decision(cli_model, monkeypatch, provider):
    monkeypatch.setenv("TEST_ANSWER", '{"rating":"Hold","price":null}')
    model = cli_model.model_copy(update={"provider": provider})
    decision = model.with_structured_output(Decision).invoke("Decide")
    assert isinstance(decision, Decision) and decision.rating == "Hold" and decision.price is None


def test_schema_conversion_keeps_nullability_without_mutating_original():
    original = Decision.model_json_schema()
    strict = _strict_schema(original)
    assert strict["required"] == ["rating", "price"]
    assert strict["additionalProperties"] is False
    assert {s["type"] for s in strict["properties"]["price"]["anyOf"]} == {"number", "null"}
    assert original["properties"]["price"]["default"] is None


@pytest.mark.parametrize("payload", [
    {"content": "", "tool_calls": [{"name": "delete_files", "arguments": "{}"}]},
    {"content": "", "tool_calls": [{"name": "quote", "arguments": "[]"}]},
    {"content": 3, "tool_calls": []},
])
def test_invalid_output_is_refused(cli_model, monkeypatch, payload):
    monkeypatch.setenv("TEST_ANSWER", json.dumps(payload))
    with pytest.raises(ValueError):
        cli_model.bind_tools([quote]).invoke("Analyze")


@pytest.mark.parametrize("provider", ["codex_cli", "agy_cli"])
def test_native_tool_usage_is_refused(cli_model, monkeypatch, provider):
    monkeypatch.setenv("TEST_NATIVE_TOOL", "1")
    with pytest.raises(RuntimeError, match="native tool"):
        cli_model.model_copy(update={"provider": provider}).invoke("Analyze")


def test_agy_schema_finish_is_not_an_external_tool(cli_model, monkeypatch):
    monkeypatch.setenv("TEST_FINISH", "1")
    assert cli_model.model_copy(update={"provider": "agy_cli"}).invoke("Analyze").content == "report"


def test_agy_refuses_long_history_without_truncation(cli_model):
    with pytest.raises(ValueError, match="120 KB"):
        cli_model.model_copy(update={"provider": "agy_cli"}).invoke("x" * 120_001)


def test_timeout_terminates_process(cli_model, monkeypatch):
    monkeypatch.setenv("TEST_DELAY", "10")
    with pytest.raises(TimeoutError):
        cli_model.model_copy(update={"timeout": 0.2}).invoke("Analyze")


def test_cli_failure_never_becomes_a_report(cli_model, monkeypatch):
    monkeypatch.setenv("TEST_EXIT", "2")
    with pytest.raises(RuntimeError, match="sign-in"):
        cli_model.invoke("Analyze")


def test_missing_completion_is_not_success(cli_model, monkeypatch):
    monkeypatch.setenv("TEST_NO_COMPLETION", "1")
    with pytest.raises(RuntimeError, match="complete"):
        cli_model.invoke("Analyze")


def test_factory_and_split_tiers_use_cli_without_api_keys(cli_model, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_CODEX_BIN", cli_model.executable)
    monkeypatch.setenv("TRADINGAGENTS_AGY_BIN", cli_model.executable)
    for provider in ("codex_cli", "agy_cli"):
        assert create_llm_client(provider, "default").get_llm().provider == provider
    config = {"llm_provider": "codex_cli", "quick_think_llm": "default",
              "deep_think_provider": "agy_cli", "deep_think_llm": "default"}
    assert create_tier_client(config, "quick").get_llm().provider == "codex_cli"
    assert create_tier_client(config, "deep").get_llm().provider == "agy_cli"


@pytest.mark.parametrize(("message", "expected"), [
    ("You have hit your usage limit. private-key-do-not-display", "usage limit reached"),
    ("Unauthorized: private-key-do-not-display", "sign-in required"),
])
def test_known_cli_errors_are_actionable_without_echoing_provider_secrets(cli_model, monkeypatch, message, expected):
    monkeypatch.setenv("TEST_EXIT", "1")
    monkeypatch.setenv("TEST_ERROR_MESSAGE", message)
    with pytest.raises(RuntimeError, match=expected) as error:
        cli_model.invoke("Analyze")
    assert "private-key-do-not-display" not in str(error.value)
