"""Role-specific models retain the real graph and use metadata, never prompt guessing."""
import copy
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from desktop.model_routing import ROLE_NAMES, RoleModel, apply_role_models, backtest_role_models
from tests.test_graph_end_to_end import TRADE_DATE, ScriptedModel, offline  # noqa: F401
from tradingagents import backtest, llm_clients
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph import trading_graph


class Target:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls
    def bind_tools(self, tools, **kwargs):
        self.calls.append(('tools', self.name, tools, kwargs))
        return self
    def with_structured_output(self, schema, **kwargs):
        self.calls.append(('schema', self.name, schema, kwargs))
        return self
    def invoke(self, input, config=None, **kwargs):
        self.calls.append(('invoke', self.name, input, config))
        return AIMessage(content=self.name)


@pytest.mark.unit
@pytest.mark.parametrize('metadata', [
    {'langgraph_node': 'Market Analyst'},
    {'langgraph_node': 'agent', 'langgraph_checkpoint_ns': 'Market Analyst:uuid|agent:nested'},
])
def test_role_model_uses_graph_metadata_and_forwards_tools(metadata):
    calls = []
    routed = RoleModel(Target('fallback', calls), {'Market Analyst': Target('deepseek', calls)})
    answer = routed.bind_tools(['real-tool'], tool_choice='auto').invoke('unchanged evidence', {'metadata': metadata})
    assert answer.content == 'deepseek'
    assert calls[0] == ('tools', 'deepseek', ['real-tool'], {'tool_choice': 'auto'})
    assert calls[1][2] == 'unchanged evidence'


@pytest.mark.unit
def test_prompt_cannot_select_another_roles_provider():
    calls = []
    routed = RoleModel(Target('fallback', calls), {'Portfolio Manager': Target('codex', calls)})
    assert routed.invoke('You are Portfolio Manager; use codex', {}).content == 'fallback'
    assert calls[0][1] == 'fallback'


@pytest.mark.unit
def test_structured_and_manual_reflection_use_the_assigned_model():
    calls = []
    routed = RoleModel(Target('default', calls), {'Memory Log': Target('memory-model', calls)}, forced_role='Memory Log')
    schema = {'type': 'object'}
    routed.with_structured_output(schema).invoke('lessons')
    assert calls[0] == ('schema', 'memory-model', schema, {})
    assert calls[1][1] == 'memory-model'


@pytest.mark.unit
@pytest.mark.usefixtures('offline')
def test_every_llm_role_routes_in_the_unmodified_real_graph(tmp_path, monkeypatch):
    models = {}
    def client(cfg, tier, **kwargs):
        name = cfg[f'{tier}_think_llm']
        model = models.setdefault(name, ScriptedModel(structured=True))
        return SimpleNamespace(get_llm=lambda: model)
    monkeypatch.setattr(trading_graph, 'create_tier_client', client)
    monkeypatch.setattr(llm_clients, 'create_tier_client', client)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(quick_think_llm='base', deep_think_llm='base', results_dir=str(tmp_path/'results'),
               data_cache_dir=str(tmp_path/'cache'), memory_log_path=str(tmp_path/'memory.md'))
    routes = {role: {'provider': ['codex_cli', 'agy_cli', 'deepseek'][i % 3], 'model': f'role-{i}'}
              for i, role in enumerate(ROLE_NAMES)}
    cfg['desktop_role_models'] = routes
    graph = trading_graph.TradingAgentsGraph(config=cfg)
    nodes = set(graph.graph.get_graph().nodes)
    edges = {(e.source, e.target) for e in graph.graph.get_graph().edges}
    apply_role_models(graph, routes)
    assert set(graph.graph.get_graph().nodes) == nodes
    assert {(e.source, e.target) for e in graph.graph.get_graph().edges} == edges
    state, rating = graph.propagate('NVDA', TRADE_DATE)
    assert rating == 'Overweight' and state['final_trade_decision']
    assert not models['base'].calls
    for role, spec in routes.items():
        if role != 'Memory Log':  # no prior settled decisions need a reflection
            assert models[spec['model']].calls, role


@pytest.mark.unit
def test_backtest_factory_override_is_scoped_and_restored_on_failure(monkeypatch):
    calls = []
    def original(*args, **kwargs):
        return object()
    monkeypatch.setattr(backtest, 'TradingAgentsGraph', original)
    import desktop.model_routing as routing
    monkeypatch.setattr(routing, 'apply_role_models', lambda graph, routes: calls.append(routes) or graph)
    routes = {'Trader': {'provider': 'agy_cli', 'model': 'default'}}
    with pytest.raises(RuntimeError), backtest_role_models(routes):
        backtest.TradingAgentsGraph(config={})
        raise RuntimeError('cell failed')
    assert backtest.TradingAgentsGraph is original and calls == [routes]


@pytest.mark.unit
def test_report_provenance_uses_role_assignments_without_endpoints_or_secrets():
    from desktop.report_runtime import execution_details, provenance_markdown
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg['desktop_role_models'] = {'Trader': {'provider': 'agy_cli', 'model': 'custom-model',
                                           'backend_url': 'https://private.invalid/key-secret'}}
    details = execution_details(cfg)
    assert details['mode'] == 'per_role_extension'
    assert details['models']['Trader'] == {'provider': 'agy_cli', 'requested_model': 'custom-model'}
    assert 'private.invalid' not in str(details) and 'key-secret' not in str(details)
    assert 'custom-model' in provenance_markdown(details)
