"""Bounded, resumable selection from an explicit inventory; never open model paths."""
import logging
import re

from .errors import ContextBudgetExceeded, ReviewError
from .narrative import Narrative
from .synthesis import digest

log = logging.getLogger(__name__)


def partition(entries, payload, schema, provider):
    """Fill the configured request budget, without a fixed entry-count ceiling."""
    pages, offset = [], 0
    while offset < len(entries):
        provider.control.check()
        if not provider.fits('', payload(entries[offset:offset + 1]), schema):
            raise ContextBudgetExceeded('One inventory entry or source sample cannot fit; adjust input/output budgets')
        # Request size grows with added entries. Binary search avoids repeatedly
        # serializing every growing prefix in inventories of thousands of files.
        lower, upper = offset + 1, len(entries)
        while lower < upper:
            provider.control.check()
            middle = (lower + upper + 1) // 2
            if provider.fits('', payload(entries[offset:middle]), schema):
                lower = middle
            else:
                upper = middle - 1
        pages.append(entries[offset:lower])
        offset = lower
    return pages


def select_files(inventory, provider, max_files, nodes, checkpoint, schema, context=None):
    files = {entry['id']: entry['path'] for entry in inventory}
    entries = inventory
    auditing = 'audit_selection' in schema['properties']
    for level in range(20):
        def payload(page):
            data = {'stage': 'prioritize_security_files' if auditing else 'choose_representative_files',
                    'inventory': [{'id': entry['id'], 'path': entry['path']} for entry in page],
                    'selection_limit': max_files, 'selection_attempt': 1,
                    'goal': ('Prioritize security-sensitive code and trust boundaries across modules; give a reason per choice.' if auditing else
                             'Broad codebase rundown: cover distinct modules, manifests, documentation, entry points and core behavior.')}
            if context:
                data['codebase_rundown'] = context
            return data
        pages = partition(entries, payload, schema, provider)
        log.info('File selection round %d (%s): %d candidates from %d available files across %d pages',
                 level + 1, 'inventory' if level == 0 else 'shortlist', len(entries), len(inventory), len(pages))
        selected = []
        for page_number, page in enumerate(pages, 1):
            data = payload(page)
            if len(pages) > 1:
                data['selection_limit'] = min(max_files, max(1, len(page) // 2))
            key = digest(data)
            saved = nodes.get(key, {})
            if saved.get('complete'):
                log.info('Reusing file selection round %d, page %d/%d', level + 1, page_number, len(pages))
                selected.extend(saved['selected_ids'])
                continue
            log.info('File selection round %d, page %d/%d: %d entries, choosing up to %d files',
                     level + 1, page_number, len(pages), len(page), data['selection_limit'])
            data['selection_attempt'] = saved.get('attempt', 0) + 1
            response = provider.ask('', data, schema)
            if not isinstance(response, Narrative):
                raise ReviewError('File selection requires a text response')
            node = {**response.record(), 'complete': False, 'attempt': data['selection_attempt'],
                    'paths': [entry['path'] for entry in page], 'selected_ids': []}
            nodes[key] = node
            checkpoint()
            ids = list(dict.fromkeys(int(v) for v in re.findall(r'(?im)^\s*(?:[-*]\s*)?FILE\s+(\d+)\b', str(response))))
            allowed = {entry['id'] for entry in page}
            if not response.complete or not ids or len(ids) > data['selection_limit'] or not set(ids) <= allowed:
                raise ReviewError('File selection was unfinished or invalid; expected FILE <id> lines from the supplied inventory within selection_limit. Response saved; rerun to retry.')
            node.update(complete=True, selected_ids=ids)
            selected.extend(ids)
            checkpoint()
        selected = list(dict.fromkeys(selected))
        log.info('File selection round %d retained %d candidates; final file limit %d', level + 1, len(selected), max_files)
        if len(selected) <= max_files:
            return [files[i] for i in selected]
        if len(selected) >= len(entries):
            raise ReviewError('Inventory cannot be narrowed within this input budget; increase input limits or narrow --include scope')
        selected_set = set(selected)
        entries = [entry for entry in entries if entry['id'] in selected_set]
    raise ReviewError('File selection reduction limit reached; narrow the source scope')
