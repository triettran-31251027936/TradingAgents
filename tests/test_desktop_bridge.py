"""Desktop boundaries preserve upstream research, persistence and report safety."""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from desktop import bridge
from tradingagents.backtest import BacktestResult, BacktestSummary, RatingScore
from tradingagents.dataflows.config import get_config
from tradingagents.graph import trading_graph
from tradingagents.memory import TradingMemoryLog

TRADE_DATE = '2026-01-09'


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    paths = {'results_dir': str(tmp_path / 'results'),
             'data_cache_dir': str(tmp_path / 'cache'),
             'memory_log_path': str(tmp_path / 'memory.md')}
    monkeypatch.setattr(bridge, 'CONFIG_FILE', tmp_path / 'config/desktop.json')
    monkeypatch.setattr(bridge, 'MANAGED', paths)
    cfg = copy.deepcopy(bridge.DEFAULT_CONFIG)
    cfg.update(paths, checkpoint_enabled=True)
    events = []
    monkeypatch.setattr(bridge, 'emit', lambda kind, payload: events.append((kind, payload)))
    return cfg, events


def complete_state():
    report = '# Evidence\n\nA readable **report**.\n\n| Metric | Value |\n| --- | --- |\n| Count | 1 |\n'
    return {
        'company_of_interest': 'FPT.VN', 'trade_date': TRADE_DATE,
        'market_report': report, 'sentiment_report': report, 'news_report': report,
        'fundamentals_report': report, 'investment_plan': report,
        'trader_investment_plan': report,
        'investment_debate_state': {'bull_history': report, 'bear_history': report,
                                    'history': report, 'current_response': report},
        'risk_debate_state': {'aggressive_history': report, 'conservative_history': report,
                             'neutral_history': report, 'history': report},
        'final_trade_decision': '**Rating**: Hold\n\n' + report,
        'final_rating': 'Hold', 'past_context': 'Previous resolved lesson', 'memory_note': '',
    }


def fake_stream_graph(monkeypatch, *, resume=False, fail=False):
    """Keep the actual memory writer/state log/report writer; script only stream IO."""
    class Graph(trading_graph.TradingAgentsGraph):
        instances = []

        def __init__(self, selected_analysts, *, config, callbacks):
            self.selected_analysts = selected_analysts
            self.config = config
            self.callbacks = callbacks
            self.memory_log = TradingMemoryLog(config)
            self.calls = []
            self.propagator = SimpleNamespace(get_graph_args=lambda **kw: {
                'stream_mode': 'values', 'config': {'recursion_limit': 100, **kw}})
            self.instances.append(self)

        @contextmanager
        def checkpoint_scope(self, ticker, day, asset, portfolio):
            self.calls.append(('enter', ticker, day, asset, portfolio))
            try:
                yield 'desktop-thread'
            finally:
                self.calls.append(('exit',))

        def create_run_state(self, ticker, day, asset, portfolio):
            self.calls.append(('initial', ticker, day, asset, portfolio))
            return {'initial': True}

        def checkpoint_input(self, initial):
            self.calls.append(('resume_input', initial))
            return None if resume else initial

        def stream_run(self, graph_input, **args):
            self.calls.append(('stream', graph_input, args))
            assert get_config()['memory_log_path'] == self.config['memory_log_path']
            assert args['config']['configurable']['thread_id'] == 'desktop-thread'
            assert args['config']['recursion_limit'] == 100
            assert args['config']['callbacks']
            yield [], {'past_context': '', 'memory_note': ''}
            yield [], {'market_report': complete_state()['market_report']}
            # The same report can be yielded again by a top-level state update;
            # it should only be published once, without repeating its content.
            yield [], {'market_report': complete_state()['market_report']}
            if fail:
                raise RuntimeError('provider unavailable')
            yield [], complete_state()

        def record_decision(self, ticker, day, state):
            self.calls.append(('record', ticker, day))
            super().record_decision(ticker, day, state)

        def clear_checkpoint_on_success(self, ticker, day, asset, portfolio):
            self.calls.append(('clear', ticker, day, asset, portfolio))

        def save_reports(self, state, ticker, target, html=True):
            self.calls.append(('save',))
            return super().save_reports(state, ticker, target, html=html)

    monkeypatch.setattr(trading_graph, 'TradingAgentsGraph', Graph)
    return Graph


@pytest.mark.unit
@pytest.mark.parametrize('resume', [False, True], ids=['fresh', 'resume'])
def test_streamed_analysis_retains_checkpoint_memory_and_original_reports(desktop, monkeypatch, resume):
    cfg, events = desktop
    Graph = fake_stream_graph(monkeypatch, resume=resume)
    payload = {'ticker': 'fpt.vn', 'date': TRADE_DATE,
               'analysts': ['market', 'social', 'news', 'fundamentals'],
               'portfolio': {'cash': 1000, 'currency': 'VND', 'positions': []}}
    result = bridge.analyze(payload, cfg)
    graph = Graph.instances[0]
    calls = [call[0] for call in graph.calls]
    assert calls == ['enter', 'initial', 'resume_input', 'stream', 'record', 'clear', 'exit', 'save']
    stream_input = next(call[1] for call in graph.calls if call[0] == 'stream')
    assert stream_input is None if resume else stream_input == {'initial': True}
    assert graph.calls[0][4].cash == 1000
    assert graph.selected_analysts == payload['analysts']
    assert result['rating'] == 'Hold'
    report = Path(cfg['results_dir']) / result['report_id']
    assert report.is_file() and report.with_suffix('.html').is_file()
    assert '# Trading Analysis Report: FPT.VN' in report.read_text()
    assert (report.parent / '1_analysts/market.md').is_file()
    assert (report.parent / '4_risk/neutral.md').is_file()
    assert 'Previous resolved lesson' in (report.parent / '6_memory/memory.md').read_text()
    assert [entry['rating'] for entry in graph.memory_log.load_entries()] == ['Hold']
    assert list((Path(cfg['results_dir']) / 'FPT.VN/TradingAgentsStrategy_logs').glob('*.json'))
    emitted = [payload for kind, payload in events if kind == 'role_report']
    assert len(emitted) == 13
    assert {payload['name'] for payload in emitted} == set(bridge.ROLES)
    assert all('<table>' in payload['html'] and '<strong>' in payload['html']
               for payload in emitted if payload['name'] != 'Memory Log')
    memory = next(payload for payload in emitted if payload['name'] == 'Memory Log')
    assert '<h1>Memory &amp; Reflection</h1>' in memory['html']
    assert sum(payload['name'] == 'Market Analyst' for payload in emitted) == 1
    assert next(i for i, item in enumerate(emitted) if item['name'] == 'Memory Log') > 0


@pytest.mark.unit
def test_failed_stream_keeps_checkpoint_and_does_not_record_a_false_decision(desktop, monkeypatch):
    cfg, events = desktop
    Graph = fake_stream_graph(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match='provider unavailable'):
        bridge.analyze({'ticker': 'FPT.VN', 'date': TRADE_DATE}, cfg)
    calls = [call[0] for call in Graph.instances[0].calls]
    assert 'exit' in calls and not {'record', 'clear', 'save'} & set(calls)
    assert not Path(cfg['memory_log_path']).exists()
    assert any(kind == 'role_report' for kind, _ in events)


@pytest.mark.unit
def test_config_is_atomic_validated_and_storage_paths_stay_managed(desktop):
    cfg, _ = desktop
    result = bridge.save_config({'llm_provider': 'codex_cli', 'max_debate_rounds': 3,
                                 'results_dir': '/unrelated', 'checkpoint_enabled': True})
    assert result['llm_provider'] == 'codex_cli' and result['max_debate_rounds'] == 3
    assert result['results_dir'] == cfg['results_dir']
    assert bridge.CONFIG_FILE.is_file()
    assert not bridge.CONFIG_FILE.with_suffix('.json.tmp').exists()
    saved = bridge.CONFIG_FILE.read_text()
    for invalid in [{'unknown_option': True}, {'checkpoint_enabled': 'yes'},
                    {'max_tool_rounds': 0}, {'max_tool_rounds': 100, 'max_recur_limit': 100},
                    {'max_risk_discuss_rounds': True}]:
        with pytest.raises(ValueError):
            bridge.save_config(invalid)
        assert bridge.CONFIG_FILE.read_text() == saved


@pytest.mark.unit
@pytest.mark.parametrize('identifier', ['../complete_report.md', '/etc/complete_report.md', 'FPT.VN/readme.md'])
def test_report_lookup_rejects_escape_or_nonreport_filename(desktop, identifier):
    cfg, _ = desktop
    with pytest.raises(ValueError):
        bridge.report_path(identifier, cfg)


@pytest.mark.unit
def test_report_html_and_role_markdown_render_as_readable_untrusted_text(desktop, monkeypatch):
    cfg, _ = desktop
    monkeypatch.setattr(bridge, 'config', lambda: cfg)
    root = Path(cfg['results_dir']) / 'FPT.VN' / TRADE_DATE
    root.mkdir(parents=True)
    main = root / 'complete_report.md'
    main.write_text('# Trading Analysis Report: FPT.VN\n\n- Analysis date: ' + TRADE_DATE + '\n- Rating: Hold\n')
    section = root / '1_analysts/market.md'
    section.parent.mkdir()
    section.write_text('# Market\n\n<script>alert(1)</script>\n\n[unsafe](javascript:alert(1))\n')
    response = bridge.dispatch('report', {'id': str(main.relative_to(Path(cfg['results_dir'])))})
    rendered = response['sections'][0]['html']
    assert '<h1>Market</h1>' in rendered
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert 'href="javascript:' not in rendered
    assert response['rendered'].startswith('<h1>Trading Analysis Report: FPT.VN')
    items = bridge.history(cfg)
    assert len(items) == 1 and items[0]['ticker'] == 'FPT.VN'
    assert items[0]['date'] == TRADE_DATE and items[0]['rating'] == 'Hold'


@pytest.mark.unit
def test_html_sibling_symlink_cannot_read_outside_report_directory(desktop, monkeypatch, tmp_path):
    cfg, _ = desktop
    monkeypatch.setattr(bridge, 'config', lambda: cfg)
    root = Path(cfg['results_dir']) / 'FPT.VN' / TRADE_DATE
    root.mkdir(parents=True)
    main = root / 'complete_report.md'
    main.write_text('# Report')
    private = tmp_path / 'private.html'
    private.write_text('PRIVATE_EXTERNAL_CONTENT')
    main.with_suffix('.html').symlink_to(private)
    try:
        response = bridge.dispatch('report', {'id': str(main.relative_to(Path(cfg['results_dir'])))})
    except ValueError:
        return
    assert 'PRIVATE_EXTERNAL_CONTENT' not in response['html']
    assert response['path'] != str(main.with_suffix('.html'))


@pytest.mark.unit
def test_history_skips_partial_or_external_reports(desktop, tmp_path):
    cfg, _ = desktop
    root = Path(cfg['results_dir'])
    (root / 'partial').mkdir(parents=True)
    (root / 'partial/complete_report.md').write_text('')
    (root / 'external').mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('# PRIVATE')
    (root / 'external/complete_report.md').symlink_to(outside)
    assert bridge.history(cfg) == []


@pytest.mark.unit
def test_inputs_preserve_portfolio_and_asset_specific_analysts():
    ticker, chosen, asset, portfolio = bridge.inputs({
        'ticker': 'btc-usd', 'asset_type': 'crypto',
        'analysts': ['market', 'social', 'fundamentals'],
        'portfolio': {'cash': 100, 'currency': 'USD', 'positions': [
            {'ticker': 'BTCUSD', 'quantity': 0.2, 'average_price': 50000}]}})
    assert ticker == 'BTC-USD' and asset == 'crypto'
    assert chosen == ['market', 'social']
    assert portfolio.positions[0].quantity == 0.2
    for payload in [{'ticker': '../data'}, {'ticker': 'FPT.VN', 'analysts': []},
                    {'ticker': 'FPT.VN', 'analysts': ['unknown']},
                    {'ticker': 'FPT.VN', 'asset_type': 'future'}]:
        with pytest.raises(ValueError):
            bridge.inputs(payload)
    with pytest.raises(ValidationError):
        bridge.inputs({'ticker': 'FPT.VN', 'portfolio': {'cash': 'invalid'}})


@pytest.mark.unit
def test_backtest_delegates_grid_resume_and_portfolio_to_original_api(desktop, monkeypatch):
    cfg, events = desktop
    monkeypatch.setattr(bridge, 'config', lambda: cfg)
    import tradingagents.backtest as backtest
    calls = []

    def run(tickers, dates, config, **kwargs):
        calls.append((tickers, dates, config, kwargs))
        kwargs['progress'](1, 2, tickers[0], dates[0])
        return BacktestResult(run_id='20260109_resume', log_path=Path(cfg['memory_log_path']),
                              cells_run=1, skipped=1, failures=[('FPT.VN', TRADE_DATE, 'no data')])

    monkeypatch.setattr(backtest, 'run_backtest', run)
    monkeypatch.setattr(backtest, 'summarize', lambda _: BacktestSummary(
        resolved=1, pending=1, by_rating={'Hold': RatingScore(1, None, 0.01)}))
    response = bridge.dispatch('backtest', {
        'tickers': 'fpt.vn, vnm.vn', 'start': '2026-01-02', 'end': TRADE_DATE, 'every': 7,
        'analysts': ['market', 'news'], 'run_id': '20260109_resume',
        'portfolio': {'cash': 100, 'currency': 'VND', 'positions': []}})
    tickers, dates, passed_cfg, options = calls[0]
    assert tickers == ['FPT.VN', 'VNM.VN'] and dates == ['2026-01-02', TRADE_DATE]
    assert passed_cfg == cfg and options['run_id'] == '20260109_resume'
    assert options['selected_analysts'] == ['market', 'news'] and options['portfolio'].cash == 100
    assert response['skipped'] == 1 and response['summary']['resolved'] == 1
    assert response['summary']['by_rating']['Hold']['hit_rate'] is None
    assert any(kind == 'cell' and payload['done'] == 1 for kind, payload in events)


@pytest.mark.unit
def test_empty_memory_is_explicit_and_can_be_deferred_until_node_finished():
    state = {'past_context': '', 'memory_note': ''}
    body = bridge.role_reports(state)['Memory Log']
    assert 'Chưa có bài học được cung cấp' in body
    assert 'Memory Log' not in bridge.role_reports(state, include_empty_memory=False)
    state['memory_note'] = 'Settling past decisions failed; they stay pending.'
    body = bridge.role_reports(state, include_empty_memory=False)['Memory Log']
    assert state['memory_note'] in body and 'Trạng thái settlement' in body


@pytest.mark.unit
@pytest.mark.parametrize('action, function, payload, expected_kwargs', [
    ('subscription_status', 'status', {}, {}),
    ('subscription_logout', 'logout', {}, {}),
    ('subscription_models', 'list_models', {}, {}),
    ('subscription_redirect', 'submit_redirect', {'attempt_id': 'attempt', 'url': 'callback'}, {}),
])
def test_subscription_actions_delegate_to_original_desktop_auth_adapter(
        desktop, monkeypatch, action, function, payload, expected_kwargs):
    from desktop import subscription
    cfg, _ = desktop
    monkeypatch.setattr(bridge, 'config', lambda: cfg)
    calls = []
    def perform(*args, **kwargs):
        calls.append((args, kwargs))
        return {'delegated': function}
    monkeypatch.setattr(subscription, function, perform)
    assert bridge.dispatch(action, payload) == {'delegated': function}
    expected_args = ('attempt', 'callback') if action == 'subscription_redirect' else ()
    assert calls == [(expected_args, expected_kwargs)]


@pytest.mark.unit
def test_subscription_login_receives_the_event_channel_without_reimplementing_oauth(desktop, monkeypatch):
    from desktop import subscription
    cfg, events = desktop
    monkeypatch.setattr(bridge, 'config', lambda: cfg)
    def login(emit):
        emit('subscription_pending', {'attempt_id': 'safe-id'})
        return {'signed_in': True, 'email': 'investor@example.test'}
    monkeypatch.setattr(subscription, 'login', login)
    assert bridge.dispatch('subscription_login', {})['signed_in']
    assert events == [('subscription_pending', {'attempt_id': 'safe-id'})]


@pytest.mark.unit
def test_memory_callback_marks_completion_only_after_actual_node_end(desktop):
    _, events = desktop
    handler = bridge.callback()
    handler.on_chain_start({}, {}, name='Memory Log', run_id='memory-run')
    assert 'Memory Log' not in handler.completed
    assert events == [('node', {'name': 'Memory Log', 'status': 'running'})]
    handler.on_chain_end({'past_context': ''}, run_id='memory-run')
    assert 'Memory Log' in handler.completed
    assert events[-1] == ('node', {'name': 'Memory Log', 'status': 'completed'})


@pytest.mark.unit
def test_import_does_not_load_the_desktop_profile_into_the_callers_environment(tmp_path):
    # A fresh interpreter avoids both pytest's blank settings overlay and any
    # bridge already imported during collection. An empty working directory also
    # leaves the upstream package's normal cwd-based dotenv loading out of this
    # check: it specifically catches desktop's repo-root profile being loaded
    # merely by importing its helpers. No environment values are printed.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('TRADINGAGENTS_')}
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    script = """
import os
before = dict(os.environ)
assert not any(key.startswith('TRADINGAGENTS_') for key in before)
import desktop.bridge
assert dict(os.environ) == before, 'Desktop import changed the environment; values omitted.'
print('environment-preserved')
"""
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, 'Desktop import changed the environment; values omitted.'
    assert result.stdout == 'environment-preserved\n'
    assert result.stderr == ''


@pytest.mark.unit
@pytest.mark.parametrize('nested_name', ['ChatPromptTemplate', 'RunnableSequence', 'PydanticOutputParser'])
def test_nested_chains_do_not_complete_the_role_from_inherited_metadata(desktop, nested_name):
    _, events = desktop
    handler = bridge.callback()
    handler.on_chain_start({}, {}, name='Memory Log', run_id='role',
                           metadata={'langgraph_node': 'Memory Log'})
    handler.on_chain_start({'name': nested_name}, {}, name=nested_name, run_id='nested',
                           metadata={'langgraph_node': 'Memory Log'})
    handler.on_chain_end({}, run_id='nested')
    assert 'Memory Log' not in handler.completed
    assert events == [('node', {'name': 'Memory Log', 'status': 'running'})]
    handler.on_chain_end({}, run_id='role')
    assert handler.completed == {'Memory Log'}
    assert events[-1] == ('node', {'name': 'Memory Log', 'status': 'completed'})


@pytest.mark.unit
def test_serialized_role_name_is_only_a_fallback_when_no_explicit_chain_name(desktop):
    _, events = desktop
    handler = bridge.callback()
    handler.on_chain_start({'name': 'Market Analyst'}, {}, run_id='serialized-role',
                           metadata={'langgraph_node': 'News Analyst'})
    handler.on_chain_end({}, run_id='serialized-role')
    assert handler.completed == {'Market Analyst'}
    handler.on_chain_start({'name': 'Memory Log'}, {}, name='RunnableSequence', run_id='nested',
                           metadata={'langgraph_node': 'Memory Log'})
    handler.on_chain_end({}, run_id='nested')
    assert handler.completed == {'Market Analyst'}
    assert len(events) == 2


@pytest.mark.unit
def test_analysis_reuses_one_progress_handler_for_models_and_graph(desktop, monkeypatch):
    cfg, _ = desktop
    Graph = fake_stream_graph(monkeypatch)
    original = bridge.callback
    created = []
    def make_handler():
        handler = original()
        created.append(handler)
        return handler
    monkeypatch.setattr(bridge, 'callback', make_handler)
    bridge.analyze({'ticker': 'FPT.VN', 'date': TRADE_DATE}, cfg)
    graph = Graph.instances[0]
    streamed = next(call[2] for call in graph.calls if call[0] == 'stream')
    assert len(created) == 1
    assert graph.callbacks == streamed['config']['callbacks'] == created
    assert graph.callbacks[0] is streamed['config']['callbacks'][0]


@pytest.mark.unit
@pytest.mark.parametrize('flags', [{'save': False}, {'html': False}, {'show': True}])
def test_original_report_flags_are_preserved(desktop, monkeypatch, flags):
    cfg, events = desktop
    Graph = fake_stream_graph(monkeypatch)
    result = bridge.analyze({'ticker': 'FPT.VN', 'date': TRADE_DATE, **flags}, cfg)
    assert all(result[k] == flags.get(k, default)
               for k, default in [('save', True), ('html', True), ('show', False)])
    calls = [call[0] for call in Graph.instances[0].calls]
    assert 'record' in calls and 'clear' in calls
    assert len(TradingMemoryLog(cfg).load_entries()) == 1
    assert {p['name'] for kind, p in events if kind == 'role_report'} == set(bridge.ROLES)
    if flags.get('save') is False:
        assert result['report_id'] is None and 'save' not in calls
        assert not list(Path(cfg['results_dir']).rglob('complete_report.md'))
    else:
        report = Path(cfg['results_dir']) / result['report_id']
        assert report.is_file()
        assert report.with_suffix('.html').exists() == flags.get('html', True)


@pytest.mark.unit
@pytest.mark.parametrize('flags', [{'save': 'false'}, {'html': 0}, {'show': 'yes'}])
def test_invalid_report_flags_fail_before_graph_work(desktop, monkeypatch, flags):
    cfg, _ = desktop
    Graph = fake_stream_graph(monkeypatch)
    with pytest.raises(ValueError, match='boolean'):
        bridge.analyze({'ticker': 'FPT.VN', 'date': TRADE_DATE, **flags}, cfg)
    assert not Graph.instances


@pytest.mark.unit
def test_settlement_can_update_every_pending_ticker(desktop, monkeypatch):
    cfg, _ = desktop
    monkeypatch.setattr(bridge, 'config', lambda: cfg)
    from tradingagents.memory.settlement import Settlement
    calls = []
    class Graph:
        def __init__(self, **kwargs):
            assert kwargs['config'] == cfg
        def settle_all_pending(self):
            calls.append('all')
            return Settlement()
    monkeypatch.setattr(trading_graph, 'TradingAgentsGraph', Graph)
    assert bridge.dispatch('settle', {'all': True}) is not None
    assert calls == ['all']
    with pytest.raises(ValueError, match='boolean'):
        bridge.dispatch('settle', {'all': 'yes'})


@pytest.mark.unit
def test_empty_role_overrides_do_not_change_upstream_checkpoint_signature(desktop):
    result = bridge.save_config({'desktop_role_models': {}})
    assert 'desktop_role_models' not in result
