"""Add truthful transport provenance without changing upstream report/agent code."""
import json
import os
import tempfile
from pathlib import Path

from desktop.model_routing import ROLE_NAMES


def execution_details(cfg):
    routes = cfg.get('desktop_role_models', {})
    models = {}
    for role in ROLE_NAMES:
        tier = 'deep' if role in ('Research Manager', 'Portfolio Manager') else 'quick'
        spec = routes.get(role) or {'provider': cfg.get(f'{tier}_think_provider') or cfg['llm_provider'],
                                    'model': cfg[f'{tier}_think_llm']}
        models[role] = {'provider': spec['provider'], 'requested_model': spec['model']}
    cli = {m['provider'] for m in models.values()} & {'codex_cli', 'agy_cli'}
    ignored = [k for k in ('temperature', 'max_tokens', 'llm_max_retries', 'openai_reasoning_effort',
                           'google_thinking_level', 'anthropic_effort') if cfg.get(k) not in (None, '')]
    return {'mode': 'per_role_extension' if routes else 'original_quick_deep',
            'models': models, 'memory_reflection_only_when_due': True,
            'actual_cli_model_id_verified': False if cli else None,
            'cli_api_controls_not_forwarded': ignored if cli else [],
            'cli_concurrency_limits': {p: int(os.environ.get('TRADINGAGENTS_CODEX_MAX_CONCURRENCY', '4')
                                            if p == 'codex_cli' else os.environ.get('TRADINGAGENTS_AGY_MAX_CONCURRENCY', '1'))
                                       for p in sorted(cli)}}


def provenance_markdown(details):
    text = '## Model selection and transport\n\n'
    text += ('Mode: per-role extension; the quick/deep labels above are only defaults.\n\n'
             if details['mode'] == 'per_role_extension' else 'Mode: original TradingAgents quick/deep allocation.\n\n')
    text += '| Role | Provider | Requested model |\n| --- | --- | --- |\n'
    for role, model in details['models'].items():
        values = [role, model['provider'], model['requested_model']]
        text += '| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in values) + ' |\n'
    text += '\nMemory uses Quick only when settlement needs a reflection. '
    text += 'A CLI model named `default` is chosen by that CLI; Quick and Deep can resolve to the same model.\n'
    if details['cli_api_controls_not_forwarded']:
        text += '\nAPI controls not forwarded by the CLI adapter: ' + ', '.join(details['cli_api_controls_not_forwarded']) + '.\n'
    if details['cli_concurrency_limits']:
        text += '\nCLI concurrency limits: ' + ', '.join(f'{k}={v}' for k, v in details['cli_concurrency_limits'].items()) + '.\n'
    return text


def enrich_report(path, ticker, state, settings, cfg, html=True):
    from tradingagents.report_html import render_report
    from tradingagents.reporting import _report_parts
    details = execution_details(cfg)
    appendix = provenance_markdown(details)
    (path.parent / 'execution.json').write_text(json.dumps(details, indent=2), encoding='utf-8')
    body = path.read_text(encoding='utf-8')
    if details['mode'] == 'per_role_extension':
        title, rest = body.split('\n', 1)
        body = title + '\n\n> This run uses per-role model assignments. See the model-selection appendix.\n' + rest
    path.write_text(body + '\n\n' + appendix, encoding='utf-8')
    if html:
        parts = _report_parts(state) + [('VI. Model Selection and Transport', 'execution',
                                        [('Execution metadata', 'runtime.md', appendix)])]
        path.with_suffix('.html').write_text(render_report(ticker, state, settings, parts), encoding='utf-8')
    if state.get('_desktop_council'):
        write_council(path, state['_desktop_council'], {'ticker': ticker, 'upstream_state': state}, settings, html=html)
    return details


def write_council(path, result, evidence, settings, html=True):
    """Append review idempotently; retain the original recommendation and header."""
    from desktop.council import LABELS, PROVIDERS, _atomic, report
    from tradingagents.report_html import render_report
    from tradingagents.reporting import _report_parts
    folder = path.parent / '7_council'
    if folder.is_symlink():
        raise ValueError('Thư mục báo cáo hội đồng không hợp lệ.')
    folder.mkdir(exist_ok=True)
    backup = folder / 'original.json'
    if not backup.exists():
        _atomic(backup, {'markdown': path.read_text(encoding='utf-8')})
    body = json.loads(backup.read_text(encoding='utf-8'))['markdown']
    appendix = report(result)
    path.write_text(body + '\n\n' + appendix, encoding='utf-8')
    _atomic(folder / 'transcript.json', result)
    metadata_path = path.parent / 'execution.json'
    if metadata_path.exists() and not metadata_path.is_symlink():
        details = json.loads(metadata_path.read_text(encoding='utf-8'))
        details['council'] = {'status': result['status'], 'models': result['config']['models'],
                              'evidence_sha256': result['evidence_sha256'], 'protocol': result['protocol']}
        _atomic(metadata_path, details)
    names = {'codex_cli': 'codex', 'agy_cli': 'antigravity', 'deepseek': 'deepseek'}
    for provider in PROVIDERS:
        text = '# ' + LABELS[provider] + '\n\n'
        for stage, turns in result['turns'].items():
            text += f"## {stage}\n\n{turns[provider]['analysis']}\n\n"
        text += '## Kiểm tra cuối\n\n' + json.dumps(result['checks'][provider], ensure_ascii=False, indent=2)
        (folder / (names[provider] + '.md')).write_text(text, encoding='utf-8')
    (folder / 'council.md').write_text(appendix, encoding='utf-8')
    if html:
        metadata = path.parent / 'execution.json'
        parts = _report_parts(evidence['upstream_state'])
        if metadata.exists():
            parts += [('VI. Model Selection and Transport', 'execution', [
                ('Execution metadata', 'runtime.md', provenance_markdown(json.loads(metadata.read_text())))])]
        parts += [('VII. Hội đồng phản biện ba nhà cung cấp', 'council', [('Báo cáo và trao đổi', 'council.md', appendix)])]
        path.with_suffix('.html').write_text(render_report(evidence['ticker'], evidence['upstream_state'], settings, parts), encoding='utf-8')


def transient_report(ticker, state, settings, cfg, render):
    from tradingagents.reporting import write_report_tree
    Path(cfg['data_cache_dir']).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cfg['data_cache_dir'], prefix='preview-') as folder:
        path = write_report_tree(state, ticker, folder, settings=settings, html=False)
        enrich_report(path, ticker, state, settings, cfg, html=False)
        text = path.read_text(encoding='utf-8')
        sections = [{'name': p.stem, 'group': p.parent.name, 'text': p.read_text(encoding='utf-8'),
                     'html': render(p.read_text(encoding='utf-8'))} for p in sorted(Path(folder).glob('*/*.md'))]
    return {'markdown': text, 'rendered': render(text), 'sections': sections, 'html': ''}
