"""Malformed endpoint responses preserve the checkpoint and paid attempt."""

import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage

from desktop.runtime_models import Runtime, TokenUsage
from tradingagents.llm_clients.openai_client import DeepSeekChatOpenAI, DeepSeekResponseError


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"choices": []},
        {"choices": [None]},
        {"error": {"message": "synthetic-private-key-must-not-echo"}},
    ],
)
def test_invalid_completion_shape_never_exposes_response_body(body):
    model = DeepSeekChatOpenAI(model="deepseek-flash", api_key="synthetic-test-key")
    with pytest.raises(DeepSeekResponseError) as exc:
        model._create_chat_result(body)
    assert "synthetic-private" not in str(exc.value)


@pytest.mark.parametrize("body", [b"null", b"", b"\n\n"])
def test_http_200_without_completion_is_one_failed_paid_attempt_and_survives_resume(tmp_path, body):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    cfg = {
        "data_cache_dir": str(tmp_path),
        "desktop_budget": {"enabled": True, "deepseek_max_requests": 1},
        "desktop_role_models": {
            "Market Analyst": {"provider": "deepseek", "model": "deepseek-flash"}
        },
    }
    runtime, usage = Runtime(cfg), TokenUsage()
    saved = runtime.bind_checkpoint("same-original-graph-thread", usage, resuming=False)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = DeepSeekChatOpenAI(
            model="deepseek-flash",
            api_key="synthetic-test-key",
            base_url="https://not-a-real-endpoint.invalid",
            max_tokens=4096,
            reasoning_effort="low",
            max_retries=0,
            http_client=client,
            callbacks=[usage],
        )
        with pytest.raises(DeepSeekResponseError):
            runtime.invoke(
                "Market Analyst",
                cfg["desktop_role_models"]["Market Analyst"],
                model,
                [HumanMessage(content="Synthetic evidence only")],
                {},
            )
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 4096
    assert "max_completion_tokens" not in requests[0]
    assert requests[0]["reasoning_effort"] == "low"
    restored, receipts = Runtime(cfg), TokenUsage()
    restored.bind_checkpoint("same-original-graph-thread", receipts, resuming=True)
    assert saved.path.exists()
    assert restored.paid_budget.calls == 1
    assert receipts.summary()["unreported_requests"] == 1
    assert receipts.items[0]["error_type"] == "DeepSeekResponseError"
    with pytest.raises(ValueError, match="giới hạn"):
        restored.paid_budget.reserve("deepseek")
    assert not restored.decision


@pytest.mark.parametrize("body", [b"null", b"\n\n"])
def test_async_empty_response_also_stops_without_retry(body):
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = DeepSeekChatOpenAI(
                model="deepseek-flash",
                api_key="synthetic-test-key",
                base_url="https://not-a-real-endpoint.invalid",
                max_retries=0,
                http_async_client=client,
            )
            with pytest.raises(DeepSeekResponseError):
                await model.ainvoke("Synthetic evidence only")

    asyncio.run(run())
    assert len(calls) == 1
