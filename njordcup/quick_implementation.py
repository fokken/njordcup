"""Standalone, model-selected shallow codebase descriptions, separate from audits."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging

from .errors import ContextBudgetExceeded, ReviewError, RunStopped
from .flyover import fingerprint, provider_identity
from .memory import save_memory
from .narrative import Narrative
from .synthesis import digest, synthesize
from .selection import partition as partition_entries, select_files

log = logging.getLogger(__name__)
SELECT = {'type': 'object', 'properties': {'quick_selection': {'type': 'string'}}}
DESCRIBE = {'type': 'object', 'properties': {'quick_implementation': {'type': 'string'}}}


def analyze(sources, targets, provider, path, max_files=50, refresh=False):
    if type(max_files) is not int or max_files < 1:
        raise ValueError('max_files must be a positive integer')
    settings = {'provider': provider_identity(provider), 'max_files': max_files,
                **{k: getattr(provider, k) for k in ('max_tokens', 'max_input_chars', 'context_window',
                                                   'bytes_per_token', 'token_margin')}}
    signature = fingerprint(sources, targets)
    old = json.loads(path.read_text()) if path.is_file() and not refresh else {}
    reusable = (isinstance(old, dict) and old.get('version') == 1 and
                old.get('kind') == 'quick_implementation_analysis' and
                old.get('fingerprint') == signature and old.get('settings') == settings)
    report = deepcopy(old) if reusable else {
        'version': 1, 'kind': 'quick_implementation_analysis', 'fingerprint': signature,
        'settings': settings, 'selection_nodes': {}, 'inspection_nodes': {}, 'selected_files': [],
        'selection_complete': False, 'synthesis': {}, 'text': ''}
    report.update(status='incomplete', errors=[])
    report.pop('stop_reason', None)
    inventory = [{'id': i, 'path': p, 'lines': len(sources[p].splitlines()), 'characters': len(sources[p])}
                 for i, p in enumerate(sorted(targets), 1)]

    def checkpoint():
        inspected = {p for node in report['inspection_nodes'].values() for p in node['paths']}
        completed = {p for node in report['inspection_nodes'].values() if node['complete'] for p in node['paths']}
        shown = {p for node in report['selection_nodes'].values() for p in node['paths']}
        report['coverage'] = {'available_files': len(inventory), 'inventory_files_shown': len(shown),
                              'file_limit': max_files, 'selected_files': len(report['selected_files']),
                              'files_inspected': len(inspected), 'files_described': len(completed),
                              'files_not_inspected': len(inventory) - len(inspected)}
        report['scope_note'] = ('A broad, shallow implementation overview from model-selected file prefixes. '
                                'Omitted files and omitted portions were not inspected. This is not a security audit '
                                'or exhaustive code coverage, and is not automatically reused by security reviews.')
        report['saved_at'] = datetime.now(timezone.utc).isoformat()
        report['usage_this_invocation'] = {k: getattr(provider, k, 0) for k in
                                            ('calls', 'input_tokens', 'output_tokens', 'cache_hits', 'retries')}
        save_memory(path, report)

    def partition(entries, payload, schema):
        return partition_entries(entries, payload, schema, provider)

    def choose(entries):
        return select_files(entries, provider, max_files, report['selection_nodes'], checkpoint, SELECT)

    checkpoint()
    try:
        if not inventory:
            report.update(status='complete', selection_complete=True, text='No eligible target files to describe.')
            checkpoint()
            return report
        if not report['selection_complete']:
            for recovery in range(4):
                before = provider.server_input_chars
                try:
                    report['selected_files'] = choose(inventory)
                    report['selection_complete'] = True
                    checkpoint()
                    break
                except ContextBudgetExceeded:
                    if recovery == 3 or provider.server_input_chars == before:
                        raise
        # No source outside this fixed selection is ever sent to the model.
        described = {p for node in report['inspection_nodes'].values() if node['complete'] for p in node['paths']}
        pending = [p for p in report['selected_files'] if p not in described]
        for recovery in range(4):
            before = provider.server_input_chars
            try:
                samples = []
                for name in pending:
                    cap = min(6000, len(sources[name]))
                    while True:
                        sample = {'path': name, 'source': sources[name][:cap], 'start_line': 1,
                                  'characters_shown': cap, 'total_characters': len(sources[name]),
                                  'truncated': cap < len(sources[name])}
                        if provider.fits('', {'stage': 'describe_samples', 'source_samples': [sample]}, DESCRIBE):
                            break
                        if cap <= 128:
                            raise ContextBudgetExceeded('Quick implementation source sample cannot fit; adjust input/output budgets')
                        cap //= 2
                    samples.append(sample)
                payload = lambda page: {'stage': 'describe_samples', 'source_samples': page}
                for page in partition(samples, payload, DESCRIBE):
                    log.info('Quick implementation: inspecting %d selected file samples', len(page))
                    response = provider.ask('', payload(page), DESCRIBE)
                    if not isinstance(response, Narrative):
                        raise ReviewError('Quick implementation analysis requires prose')
                    key = digest(page)
                    report['inspection_nodes'][key] = {**response.record(), 'paths': [s['path'] for s in page],
                                                       'samples': [{k: v for k, v in s.items() if k != 'source'} for s in page]}
                    checkpoint()
                    if not response.complete:
                        raise ReviewError('Quick implementation response was unfinished; saved for resumption')
                    pending = [p for p in pending if p not in {s['path'] for s in page}]
                break
            except ContextBudgetExceeded:
                if recovery == 3 or provider.server_input_chars == before:
                    raise
        data = {'kind': 'quick_implementation_analysis', 'purpose': 'One collective, broad codebase rundown for the user, including the role of every selected file; not a security audit.',
                'coverage': report['coverage'], 'scope_note': report['scope_note'],
                'selected_files': report['selected_files'],
                'observations': [node for node in report['inspection_nodes'].values() if node['complete']]}
        state = synthesize(data, report['synthesis'], provider, checkpoint)
        report['text'] = state.get('text', '')
        report['status'] = state['status']
        if state.get('error'):
            report['errors'].append(state['error'])
        checkpoint()
    except ReviewError as exc:
        report['errors'].append(str(exc))
        if isinstance(exc, RunStopped):
            report['stop_reason'] = exc.reason
        checkpoint()
        log.warning('Quick implementation analysis incomplete: %s', exc)
    return report


def render_html(report):
    from .reporting import document, e
    if not isinstance(report, dict) or report.get('kind') != 'quick_implementation_analysis' or report.get('version') != 1:
        raise ReviewError('Expected a saved quick implementation analysis')
    try:
        coverage = report['coverage']
        selected = ''.join(f'<li>{e(p)}</li>' for p in report['selected_files'])
        errors = ''.join(f'<p>{e(error)}</p>' for error in report['errors'])
        observations = ''.join(f'<details><summary>{e(", ".join(node["paths"]))} '
                               f'({"complete" if node["complete"] else "unfinished"})</summary>'
                               f'<pre>{e(node["text"])}</pre></details>' for node in report['inspection_nodes'].values())
        samples = ''.join(f'<li>{e(s["path"])}: first {e(s["characters_shown"])} of {e(s["total_characters"])} characters'
                          f' ({"truncated" if s["truncated"] else "whole file"})</li>'
                          for node in report['inspection_nodes'].values() for s in node['samples'])
        return document('Quick implementation analysis', f'''<header><h1>Quick implementation analysis</h1>
<p>Status: {e(report['status'])}. {e(coverage['files_described'])} files described out of {e(coverage['available_files'])} available;
file limit {e(coverage['file_limit'])}. Inventory entries shown: {e(coverage['inventory_files_shown'])}.</p></header>
<section><h2>Codebase rundown</h2><pre>{e(report['text'] or 'Collective analysis is not complete yet.')}</pre></section>
<section><h2>Scope and limitations</h2><p>{e(report['scope_note'])}</p>{errors}</section>
<section><h2>Selected files</h2><ul>{selected}</ul><details><summary>Source samples</summary><ul>{samples}</ul></details></section>
<section><h2>Original observations</h2>{observations}</section>''')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReviewError('Malformed quick implementation analysis') from exc
