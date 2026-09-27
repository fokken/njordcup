"""Compile per-file analyses without interpreting or rewriting model prose."""


def compile_files(targets, units, results):
    files = []
    grouped = {}
    for chunk in units:
        grouped.setdefault(chunk['path'], []).append(chunk)
    for path in targets:
        chunks = grouped.get(path, [])
        saved = [results[c['id']] for c in chunks if c['id'] in results]
        responses = {a['id']: a for r in saved for a in r.get('narrative_analysis', [])}
        parts = []
        for analysis in responses.values():
            selected = [c for c in chunks if c['id'] in analysis['target_chunks']]
            parts.append({**analysis, 'start': min(c['start'] for c in selected),
                          'end': max(c['end'] for c in selected)})
        parts.sort(key=lambda a: (a['start'], a['end']))
        complete = all(results.get(c['id'], {}).get('status') == 'complete' for c in chunks)
        files.append({'path': path, 'paths': [path], 'status': 'complete' if complete else 'incomplete',
                      'complete': complete, 'parts': parts, 'text': join_parts(parts),
                      'chunks_total': len(chunks), 'chunks_reviewed': sum(r['status'] == 'complete' for r in saved),
                      'note': 'No source chunks selected for this file in this review.' if not chunks else ''})
    return files


def join_parts(parts):
    if len(parts) == 1:
        return parts[0]['text']
    return '\n\n'.join(f"--- Lines {a['start']}-{a['end']} ---\n{a['text']}" for a in parts)


def merge_files(reviews):
    """One report entry per path, retaining area provenance and unique responses."""
    grouped = {}
    for area_id, report in reviews:
        for file in report.get('file_analysis', []):
            entry = grouped.setdefault(file['path'], {'path': file['path'], 'paths': file['paths'],
                                       'area_ids': [], 'complete': True, 'parts': {}, 'note': file['note'], 'candidates': []})
            entry['area_ids'].append(area_id)
            if file.get('consolidation_status') == 'complete':
                entry['candidates'].append(file)
            entry['complete'] &= file['complete']
            entry['parts'].update({p['id']: p for p in file['parts']})
    files = []
    for entry in grouped.values():
        entry['parts'] = sorted(entry['parts'].values(), key=lambda p: (p['start'], p['end']))
        entry['text'] = join_parts(entry['parts'])
        entry['consolidation_status'] = 'not_needed' if len(entry['parts']) <= 1 else 'pending'
        for candidate in entry.pop('candidates'):
            if {p['id'] for p in candidate['parts']} == {p['id'] for p in entry['parts']}:
                entry['text'] = candidate['text']
                entry['consolidation_status'] = 'complete'
                break
        if entry['consolidation_status'] == 'pending':
            entry['complete'] = False
        entry['status'] = 'complete' if entry['complete'] else 'incomplete'
        files.append(entry)
    return sorted(files, key=lambda f: f['path'])


def consolidation_input(file):
    return {'kind': 'file_security_analysis', 'path': file['path'],
            'parts': file['parts'], 'chunks_total': file['chunks_total'],
            'chunks_reviewed': file['chunks_reviewed'], 'note': file['note']}


def apply_consolidations(files, states):
    from .synthesis import saved_synthesis
    for file in files:
        file['source_complete'] = file['complete']
        file['consolidation_status'] = 'not_needed' if len(file['parts']) <= 1 else 'pending'
        if len(file['parts']) > 1:
            state = saved_synthesis(consolidation_input(file), states.get(file['path'], {}))
            if state and state.get('status') == 'complete':
                file['text'] = state['text']
                file['consolidation_status'] = 'complete'
            else:
                file['complete'] = False
                file['status'] = 'incomplete'
                file['consolidation_error'] = state.get('error', '') if state else ''
