"""The review must ingest all evidence, include all peers, and resume honestly."""
import copy
import hashlib
import json
import threading

import pytest
from langchain_core.messages import SystemMessage

from desktop import council
from desktop.report_runtime import write_council

pytestmark = pytest.mark.unit


class Models:
    def __init__(self, fail_once=None, revise=False):
        self.calls = []
        self.fail_once = fail_once
        self.revise = revise
        self.lock = threading.Lock()

    def __call__(self, provider, model, callbacks):
        owner = self

        class Model:
            def with_structured_output(self, schema):
                self.schema = schema
                return self

            def invoke(self, messages):
                prompt = messages[-1].content
                with owner.lock:
                    owner.calls.append((provider, self.schema, prompt))
                    if owner.fail_once == (provider, self.schema):
                        owner.fail_once = None
                        raise TimeoutError('private provider diagnostic')
                if self.schema is council.Reading:
                    return {'notes': 'Evidence read accurately; a supplied fact is 52,800 VND.'}
                if self.schema is council.Review:
                    return {'analysis': f'{provider}: evidence critique, with conflicting data retained.',
                            'evidence_packets': [1]}
                if self.schema is council.Synthesis:
                    return {'report': '# Advisory report\n\nThe original decision is retained. Conditions and uncertainty remain explicit.',
                            'unresolved': ['Missing source coverage.']}
                return {'verdict': 'revise' if owner.revise else 'approve',
                        'concerns': ['Check source date.'] if owner.revise else []}
        return Model()


def evidence():
    return council.dossier('FPT.VN', '2026-10-29', {
        'company_of_interest': 'FPT.VN', 'trade_date': '2026-10-29',
        'market_report': 'A supplied fact is 52,800 VND.',
        'final_trade_decision': 'Rating: Hold', 'past_context': 'Previous lesson'},
        [{'tool': 'get_stock_data', 'output': '52,800 VND'}], portfolio={'cash': 10})


def config():
    return council.settings({'enabled': True, 'rounds': 1})


def test_lossless_utf8_packets_and_all_three_read_before_any_discussion(tmp_path, monkeypatch):
    monkeypatch.setattr(council, 'PACKET_BYTES', 180)
    data = evidence()
    original = copy.deepcopy(data)
    chunks = council.packets(data)
    assert json.loads(''.join(chunks)) == original
    assert all(len(p.encode()) <= 180 for p in chunks)
    models, events = Models(), []
    result = council.run(data, config(), tmp_path, lambda k, p: events.append((k, p)), factory=models)
    readings = [x for x in models.calls if x[1] is council.Reading]
    assert len(readings) == 3 * len(chunks)
    first_review = next(i for i, x in enumerate(models.calls) if x[1] is council.Review)
    assert first_review == len(readings)
    for index, chunk in enumerate(chunks, 1):
        for peer in council.PROVIDERS:
            matching = [x for x in readings if x[0] == peer and f'Read evidence packet {index}/' in x[2]]
            assert len(matching) == 1 and matching[0][2].endswith(chunk)
    loaded = next(i for i, (k, _) in enumerate(events) if k == 'council_loaded')
    independent = next(i for i, (k, p) in enumerate(events) if k == 'council_progress' and p['stage'] == 'independent')
    assert loaded < independent
    assert all(r['hashes'] == [hashlib.sha256(c.encode()).hexdigest() for c in chunks] for r in result['receipts'].values())
    assert all(all(peer in x[2] for peer in council.PROVIDERS) for x in models.calls if 'ALL PEERS' in x[2])
    assert set(result['checks']) == set(council.PROVIDERS)
    assert data == original  # No change to graph state or original decision.


def test_failure_is_not_faked_and_resumes_only_missing_steps(tmp_path):
    data, cfg = evidence(), config()
    models = Models(fail_once=('agy_cli', council.Review))
    with pytest.raises(RuntimeError, match='Antigravity') as error:
        council.run(data, cfg, tmp_path, lambda *a: None, factory=models)
    assert 'private provider diagnostic' not in str(error.value)
    before = list(models.calls)
    result = council.run(data, cfg, tmp_path, lambda *a: None, factory=models)
    new = models.calls[len(before):]
    assert not any(x[1] is council.Reading for x in new)
    assert len([x for x in new if x[1] is council.Review and 'independent critique' in x[2]]) == 1
    assert result['status'] == 'completed'
    count = len(models.calls)
    assert council.run(data, cfg, tmp_path, lambda *a: None, factory=models) == result
    assert len(models.calls) == count
    assert (tmp_path / 'council' / result['id'] / 'evidence.json').stat().st_mode & 0o777 == 0o600
    other = copy.deepcopy(cfg)
    other['models']['deepseek'] = 'another-explicit-model'
    assert council.run(data, other, tmp_path, lambda *a: None, factory=models)['id'] != result['id']


def test_reports_keep_original_and_disagreements_and_append_only_once(tmp_path):
    data = evidence()
    models = Models(revise=True)
    result = council.run(data, config(), tmp_path / 'cache', lambda *a: None, factory=models)
    assert result['revision_performed']
    assert len([x for x in models.calls if x[1] is council.Synthesis]) == 2
    assert len([x for x in models.calls if x[1] is council.Check]) == 6
    assert result['status'] == 'completed_with_concerns'
    path = tmp_path / 'complete_report.md'
    original = '# Trading Analysis Report: FPT.VN\n\n- Rating: Hold\n\nOriginal decision.'
    path.write_text(original)
    write_council(path, result, data, {}, html=True)
    first = path.read_text()
    write_council(path, result, data, {}, html=True)
    assert path.read_text() == first and first.startswith(original)
    assert first.count('# Hội đồng phản biện —') == 1
    assert 'Đề nghị sửa' in first and 'Check source date.' in first
    assert path.with_suffix('.html').exists()
    assert {p.stem for p in (tmp_path / '7_council').glob('*.md')} == {'codex', 'antigravity', 'deepseek', 'council'}
    assert json.loads((tmp_path / '7_council/transcript.json').read_text())['evidence_sha256'] == result['evidence_sha256']


def test_evidence_captures_tools_sentiment_prefetch_and_scrubs_keys(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'private-secret-value')
    collector = council.EvidenceCollector()
    collector.on_tool_start({'name': 'prices'}, '{"ticker":"FPT.VN"}', run_id='1')
    collector.on_tool_end('52,800 VND private-secret-value', run_id='1')
    collector.on_chat_model_start({}, [[SystemMessage(content='Raw Yahoo, StockTwits and Reddit prefetched data')]], metadata={'langgraph_node': 'Sentiment Analyst'})
    payload = council.dossier('FPT.VN', '2026-10-29', {}, collector.snapshot())
    assert 'private-secret-value' not in json.dumps(payload)
    assert payload['tool_observations']['tools'][0]['output'] == '52,800 VND [redacted]'
    assert payload['tool_observations']['sentiment_prefetch_prompts']
    assert 'resumed graph' in council.dossier('FPT.VN', '2026-10-29', {}, resumed=True)['coverage']


@pytest.mark.parametrize('value', [False, [], {'enabled': 'yes'}, {'rounds': 0}, {'rounds': 4}, {'models': []}, {'models': {'other': 'x'}}, {'models': {'agy_cli': ''}}, {'unknown': 1}])
def test_invalid_council_settings_rejected(value):
    with pytest.raises(ValueError):
        council.settings(value)


def test_evidence_and_journal_refuse_symlinks(tmp_path):
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    target = tmp_path / 'link.json'
    target.symlink_to(outside)
    with pytest.raises(ValueError):
        council._atomic(target, {'overwrite': True})
    assert outside.read_text() == '{}'


def test_schema_repair_reasks_same_provider_once_without_dropping_evidence(tmp_path):
    model_factory = Models()
    calls = []
    class InvalidOnce:
        def with_structured_output(self, schema):
            self.schema = schema
            return self
        def invoke(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                return {'notes': 'x' * 2300}
            return {'notes': 'Valid evidence reading with units and dates.'}
    broken = InvalidOnce()
    def factory(provider, model, callbacks):
        return broken if provider == 'deepseek' else model_factory(provider, model, callbacks)
    events = []
    # Only the first two Reading calls use this fake. Other schemas use the
    # normal factory so the actual council still reaches its final checks.
    original = broken.with_structured_output
    def choose(schema):
        if schema is council.Reading:
            return original(schema)
        return model_factory('deepseek', 'model', ()).with_structured_output(schema)
    broken.with_structured_output = choose
    result = council.run(evidence(), config(), tmp_path, lambda k,p: events.append((k,p)), factory=factory)
    assert len(calls) == 2
    assert calls[0][-1].content == calls[1][-1].content
    assert 'previous response failed' in calls[1][0].content
    assert any(k == 'council_retry' for k,p in events)
    journal = json.loads((tmp_path/'council'/result['id']/'progress.json').read_text())
    assert journal['schema_repairs']['evidence_1:deepseek'] == 1


def test_schema_repair_is_bounded_and_does_not_accept_invalid_reading(tmp_path):
    normal, attempts = Models(), []
    class Invalid:
        def with_structured_output(self, schema):
            return self
        def invoke(self, messages):
            attempts.append(messages)
            return {'notes': 'x' * 2300}
    def factory(provider, model, callbacks):
        return Invalid() if provider == 'deepseek' else normal(provider, model, callbacks)
    with pytest.raises(RuntimeError, match='ValidationError'):
        council.run(evidence(), config(), tmp_path, lambda *a: None, factory=factory)
    assert len(attempts) == 2
    journal = json.loads(next((tmp_path/'council').glob('*/progress.json')).read_text())
    assert 'evidence_1:deepseek' not in journal['steps']
    assert set(journal['steps']) == {'evidence_1:codex_cli', 'evidence_1:agy_cli'}
