"""Carry safe source checkpoints across repository snapshots, not stale conclusions."""
from copy import deepcopy


def reusable_checkpoint(report, paths, index, affected):
    selected = set(paths) - affected
    current = {c['id']: c for p in selected for c in index['files'][p]['chunks']}
    chunks = {}
    for key, saved in report.get('chunk_results', {}).items():
        chunk = current.get(key)
        if chunk is None or saved.get('hash') != chunk['hash']:
            continue
        if any(index['files'].get(p, {}).get('hash') != h for p, h in saved.get('dependencies', {}).items()):
            continue
        chunks[key] = deepcopy(saved)
    if not chunks:
        return None
    # The agent reconstructs findings, file analyses, status and coverage from these
    # validated chunks. Old aggregate results must not appear in the active report.
    return {'status': 'incomplete', 'review_signature': report.get('review_signature'),
            'chunk_results': chunks,
            'file_consolidations': {p: deepcopy(state) for p, state in report.get('file_consolidations', {}).items()
                                    if p in selected},
            'reuse_note': 'Source and known dependency hashes validated against the current snapshot; aggregate results are rebuilt on review.'}
