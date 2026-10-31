"""A completed graph with a token-truncated argument cannot propose a trade."""

from langchain_core.messages import AIMessage

from desktop.integration import artifacts
from desktop.runtime_models import Runtime, TokenUsage


def test_truncated_required_role_blocks_proposal_without_touching_store(tmp_path):
    runtime = Runtime({"data_cache_dir": str(tmp_path)})
    runtime._capture(
        "Bear Researcher", AIMessage(content="", response_metadata={"finish_reason": "length"})
    )
    runtime.decision = {"execution": {"action": "Buy", "quantity": 100, "limit_price": 20750}}

    class Store:
        def proposal(self, *args):
            raise AssertionError("Incomplete analysis must not create a proposal")

    result = artifacts(tmp_path, None, runtime, TokenUsage(), Store(), "isolated")
    assert result["paper_proposal"]["status"] == "BLOCKED"
    assert result["report_quality"]["execution_blocking_roles"] == ["Bear Researcher"]


def test_later_complete_response_clears_that_roles_token_limit(tmp_path):
    runtime = Runtime({"data_cache_dir": str(tmp_path)})
    runtime._capture(
        "Neutral Analyst",
        AIMessage(content="partial", response_metadata={"finish_reason": "length"}),
    )
    runtime._capture(
        "Neutral Analyst",
        AIMessage(content="Complete risk argument", response_metadata={"finish_reason": "stop"}),
    )
    assert not runtime.incomplete_roles


def test_exact_cache_replay_preserves_incomplete_role_gate(tmp_path):
    cfg = {"data_cache_dir": str(tmp_path)}

    class Backend:
        calls = 0

        def invoke(self, value, config):
            self.calls += 1
            return AIMessage(content="partial", response_metadata={"finish_reason": "length"})

    backend = Backend()
    spec = {"provider": "test", "model": "isolated"}
    first = Runtime(cfg)
    first.invoke("Bear Researcher", spec, backend, "Same evidence", {})
    resumed = Runtime(cfg)
    resumed.invoke("Bear Researcher", spec, backend, "Same evidence", {})
    assert backend.calls == 1
    assert resumed.incomplete_roles == {"Bear Researcher": "output_token_limit"}
