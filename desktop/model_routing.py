"""Choose role models from LangGraph metadata, preserving its original workflow."""

from __future__ import annotations

from langchain_core.runnables import Runnable
from langchain_core.runnables.config import ensure_config

ROLE_NAMES = (
    "Market Analyst",
    "Sentiment Analyst",
    "News Analyst",
    "Fundamentals Analyst",
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
    "Trader",
    "Aggressive Analyst",
    "Conservative Analyst",
    "Neutral Analyst",
    "Portfolio Manager",
    "Memory Log",
)


class RoleModel(Runnable):
    def __init__(
        self,
        fallback,
        models,
        *,
        tools=None,
        tool_kwargs=None,
        schema=None,
        schema_kwargs=None,
        forced_role=None,
        runtime=None,
        specs=None,
    ):
        self.fallback, self.models = fallback, models
        self.tools, self.tool_kwargs = tools, tool_kwargs or {}
        self.schema, self.schema_kwargs = schema, schema_kwargs or {}
        self.forced_role = forced_role
        self.runtime, self.specs = runtime, specs or {}

    def _copy(self, **changes):
        values = {
            "fallback": self.fallback,
            "models": self.models,
            "tools": self.tools,
            "tool_kwargs": self.tool_kwargs,
            "schema": self.schema,
            "schema_kwargs": self.schema_kwargs,
            "forced_role": self.forced_role,
            "runtime": self.runtime,
            "specs": self.specs,
        }
        values.update(changes)
        return RoleModel(**values)

    def bind_tools(self, tools, **kwargs):
        return self._copy(tools=tools, tool_kwargs=kwargs)

    def with_structured_output(self, schema, **kwargs):
        return self._copy(schema=schema, schema_kwargs=kwargs, tools=None)

    def invoke(self, input, config=None, **kwargs):
        cfg = ensure_config(config)
        metadata = cfg.get("metadata", {})
        role = self.forced_role or metadata.get("langgraph_node")
        if role not in ROLE_NAMES:
            # Nested analyst graphs name their own LLM node "agent"; the
            # enclosing analyst is recorded in the checkpoint namespace.
            namespace = metadata.get("langgraph_checkpoint_ns", "")
            role = next(
                (
                    part.split(":", 1)[0]
                    for part in namespace.split("|")
                    if part.split(":", 1)[0] in ROLE_NAMES
                ),
                None,
            )
        model = self.models.get(role, self.fallback)
        schema = self.schema
        plain_sentiment = False
        if self.runtime and self.runtime.guarded_sentiment(role):
            from desktop.grounding import GroundedSentimentReport

            plain_sentiment = schema is None
            schema = GroundedSentimentReport
        if self.runtime and self.runtime.quant and role == "Portfolio Manager":
            from desktop.runtime_models import ExecutablePortfolioDecision
            from tradingagents.agents.schemas import PortfolioDecision

            if schema is PortfolioDecision:
                schema = ExecutablePortfolioDecision
        if schema is not None:
            model = model.with_structured_output(schema, **self.schema_kwargs)
        elif self.tools is not None:
            model = model.bind_tools(self.tools, **self.tool_kwargs)
        if self.runtime and role in ROLE_NAMES:
            value = self.runtime.prompt(input, role)
            result = self.runtime.invoke(
                role, self.specs.get(role, {}), model, value, cfg, schema=schema
            )
            if plain_sentiment:
                from langchain_core.messages import AIMessage

                from tradingagents.agents.schemas import render_sentiment_report

                return AIMessage(content=render_sentiment_report(result))
            return result
        return model.invoke(input, config=cfg, **kwargs)


def apply_role_models(graph, routes, callbacks=(), runtime=None):
    """Rebuild the same upstream GraphSetup with routed LLM transports only."""
    if not routes and runtime is None:
        return graph
    from tradingagents.graph.setup import GraphSetup
    from tradingagents.llm_clients import create_tier_client
    from tradingagents.memory.reflection import Reflector

    models, cache = {}, {}
    if not hasattr(graph, "_desktop_base_models"):
        graph._desktop_base_models = (graph.quick_thinking_llm, graph.deep_thinking_llm)
    for role, spec in routes.items():
        key = (
            spec["provider"],
            spec["model"],
            spec.get("backend_url"),
            spec.get("effort"),
            spec.get("max_tokens"),
        )
        if key not in cache:
            cfg = {
                **graph.config,
                "quick_think_provider": key[0],
                "quick_think_llm": key[1],
                "backend_url": None,
                "quick_think_backend_url": key[2],
            }
            if spec.get("max_tokens") is not None:
                cfg["max_tokens"] = spec["max_tokens"]
            from desktop.budget import constrain

            cfg = constrain(cfg, spec)
            cache[key] = create_tier_client(
                cfg,
                "quick",
                callbacks=list(callbacks),
                **(
                    {
                        "reasoning_effort"
                        if spec["provider"] == "deepseek"
                        else "cli_reasoning_effort": spec["effort"]
                    }
                    if spec.get("effort")
                    else {}
                ),
            ).get_llm()
        models[role] = cache[key]
    quick = RoleModel(graph._desktop_base_models[0], models, runtime=runtime, specs=routes)
    deep = RoleModel(graph._desktop_base_models[1], models, runtime=runtime, specs=routes)
    graph.quick_thinking_llm, graph.deep_thinking_llm = quick, deep
    graph.reflector = Reflector(quick._copy(forced_role="Memory Log"))
    graph.graph_setup = GraphSetup(
        quick, deep, graph.conditional_logic, graph.config["max_tool_rounds"]
    )
    graph.workflow = graph.graph_setup.setup_graph(
        graph.selected_analysts, memory_node=graph._memory_step
    )
    graph.graph = graph.workflow.compile()
    return graph


from contextlib import contextmanager  # noqa: E402


@contextmanager
def backtest_role_models(routes):
    """Use the same role transports in upstream backtest's graph factory.

    A desktop job owns one Python process, so this scoped factory override is
    isolated from other desktop requests and restores the original on failure.
    The original backtest loop, scoring, resume and settlement stay unchanged.
    """
    if not routes:
        yield
        return
    from tradingagents import backtest

    original = backtest.TradingAgentsGraph

    def factory(*args, **kwargs):
        return apply_role_models(original(*args, **kwargs), routes)

    backtest.TradingAgentsGraph = factory
    try:
        yield
    finally:
        backtest.TradingAgentsGraph = original
