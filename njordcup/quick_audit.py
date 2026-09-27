"""Model-prioritized audit scope, kept distinct from repository-wide audits."""
from copy import deepcopy
import json

from .errors import ContextBudgetExceeded, ReviewError, RunStopped
from .flyover import fingerprint, provider_identity
from .memory import save_memory
from .selection import select_files
from .synthesis import digest
from .workers import RequestGate

SCHEMA = {'type': 'object', 'properties': {'audit_selection': {'type': 'string'}}}
SCOPE_NOTE = ('This is a model-prioritized subset audit. Completion applies only to the selected target files, '
              'not the whole repository. Related source may be supplied as bounded context without counting '
              'as reviewed target code. Selection reasons and the codebase rundown are model judgments, not proof.')


def prepare(sources, targets, provider, rundown_path, memory_path, max_files=50, refresh=False):
    if type(max_files) is not int or max_files < 1:
        raise ValueError('max_files must be a positive integer')
    if not rundown_path.is_file():
        raise ReviewError('Quick audit requires a saved rundown; run --quick-implementation-analysis first or specify --quick-implementation-file')
    rundown = json.loads(rundown_path.read_text())
    source_hash = fingerprint(sources, targets)
    if (not isinstance(rundown, dict) or rundown.get('kind') != 'quick_implementation_analysis' or
            rundown.get('version') != 1 or rundown.get('status') != 'complete' or
            rundown.get('fingerprint') != source_hash or not isinstance(rundown.get('text'), str) or not rundown['text'].strip()):
        raise ReviewError('Quick rundown is incomplete or stale for this source/scope; rerun --quick-implementation-analysis with matching source filters first')
    old = json.loads(memory_path.read_text()) if memory_path.is_file() else {}
    if not isinstance(old, dict) or (old and old.get('audit_mode') != 'quick'):
        raise ReviewError('Quick audit needs separate memory; choose a new --memory path instead of overwriting an existing full audit')
    identity = {'sources': source_hash, 'rundown': digest(rundown['text']), 'max_files': max_files,
                'provider': provider_identity(provider),
                'budgets': {k: getattr(provider, k) for k in ('max_tokens', 'max_input_chars', 'context_window', 'bytes_per_token', 'token_margin')}}
    old_plan = old.get('quick_audit', {})
    valid_plan = (isinstance(old_plan, dict) and isinstance(old_plan.get('selected_files'), list) and
                  all(isinstance(p, str) for p in old_plan['selected_files']) and
                  len(old_plan['selected_files']) <= max_files and set(old_plan['selected_files']) <= set(targets))
    if not refresh and valid_plan and old_plan.get('identity') == identity:
        memory = deepcopy(old)
        plan = memory['quick_audit']
    else:
        archives = deepcopy(old.get('archives', []))
        if old:
            archives.append({k: deepcopy(v) for k, v in old.items() if k != 'archives'})
        cap = min(6000, max(256, provider.max_input_chars // 4))
        context = {'text': rundown['text'][:cap], 'truncated': len(rundown['text']) > cap,
                   'note': 'Prior shallow implementation analysis. Revalidate against source; uninspected code is not assumed safe.'}
        plan = {'identity': identity, 'selection_complete': False, 'selection_nodes': {}, 'selected_files': [],
                'candidate_files': sorted(targets), 'file_limit': max_files, 'rundown_path': str(rundown_path),
                'rundown_context': context, 'scope_note': SCOPE_NOTE, 'errors': []}
        memory = {'version': 2, 'audit_mode': 'quick', 'fingerprint': None, 'provider': provider_identity(provider),
                  'overview': {'summary': 'Quick audit file prioritization pending.', 'tech_stack': [],
                               'areas': [], 'dependencies': [], 'unknowns': [SCOPE_NOTE]},
                  'reviews': [], 'archives': archives, 'coverage': {}, 'quick_audit': plan}
    plan['errors'] = []
    plan.pop('stop_reason', None)

    def checkpoint():
        plan['selection_usage_this_invocation'] = {k: getattr(provider, k, 0) for k in
                                                   ('calls', 'input_tokens', 'output_tokens', 'cache_hits', 'retries')}
        save_memory(memory_path, memory)

    checkpoint()
    if plan['selection_complete']:
        return plan
    # The selector is always prose, even when the subsequent audit uses JSON.
    selector = provider.fork(RequestGate())
    selector.output_mode = 'prompt'
    inventory = [{'id': i, 'path': p, 'lines': len(sources[p].splitlines()), 'characters': len(sources[p])}
                 for i, p in enumerate(sorted(targets), 1)]
    try:
        for recovery in range(4):
            before = selector.server_input_chars
            try:
                plan['selected_files'] = select_files(inventory, selector, max_files, plan['selection_nodes'],
                                                     checkpoint, SCHEMA, context=plan['rundown_context']) if inventory else []
                plan['selection_complete'] = True
                checkpoint()
                break
            except ContextBudgetExceeded:
                if recovery == 3 or selector.server_input_chars == before:
                    raise
    except ReviewError as exc:
        plan['errors'].append(str(exc))
        if isinstance(exc, RunStopped):
            plan['stop_reason'] = exc.reason
        checkpoint()
    return plan


def scope_summary(plan):
    return {'available_files': len(plan.get('candidate_files', [])),
            'selected_files': plan.get('selected_files', []), 'file_limit': plan.get('file_limit'),
            'not_selected_files': len(set(plan.get('candidate_files', [])) - set(plan.get('selected_files', []))),
            'selection_complete': plan.get('selection_complete', False),
            'rundown_path': plan.get('rundown_path'), 'scope_note': plan.get('scope_note', SCOPE_NOTE),
            'rundown_context_truncated': plan.get('rundown_context', {}).get('truncated', False),
            'selection_notes': [n['text'] for n in plan.get('selection_nodes', {}).values() if n.get('complete')],
            'errors': plan.get('errors', [])}
