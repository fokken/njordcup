"""Small, file-relevant memory excerpts for narrative reviews."""
import json


def compact_memory(overview, paths, limit=3000):
    selected = set(paths)
    result = {'note': 'Selected, shortened prior-model context; not evidence. Revalidate against source.',
              'summary': overview.get('summary', '')[:400],
              'tech_stack': [s[:80] for s in overview.get('tech_stack', [])[:8]],
              'unknowns': [s[:160] for s in overview.get('unknowns', [])[:2]]}
    area = overview.get('selected_area')
    if not area:
        area = next((a for a in overview.get('areas', []) if selected.intersection(a.get('paths', []))), None)
    if area:
        result['selected_area'] = {'title': area.get('title', '')[:120],
                                   'reason': area.get('reason', '')[:240],
                                   'attack_surfaces': [s[:120] for s in area.get('attack_surfaces', [])[:3]]}
    history = []
    for review in overview.get('previous_reviews', []):
        findings = [{k: (v[:240] if isinstance(v, str) else v) for k, v in f.items()}
                    for f in review.get('findings', []) if f.get('path') in selected][:3]
        excerpts = [{'paths': sorted(selected.intersection(a['paths'])), 'text': a['text'][:400]}
                    for a in review.get('narrative_excerpts', [])
                    if isinstance(a, dict) and selected.intersection(a.get('paths', []))][:2]
        if findings or excerpts:
            history.append({'findings': findings, 'narrative_excerpts': excerpts})
        if len(history) == 2:
            break
    if history:
        result['previous_reviews'] = history
    implementation = overview.get('implementation_analysis')
    if implementation:
        pages = [{'component': p['component'], 'summary': p['summary'][:400],
                  'data_flows': [s[:160] for s in p.get('data_flows', [])[:2]]}
                 for p in implementation.get('pages', []) if selected.intersection(p.get('paths', []))][:2]
        if pages:
            result['implementation_analysis'] = {'pages': pages}
    # JSON escaping and very long paths must also fit the memory allowance.
    while len(json.dumps(result)) > limit:
        for key in ('previous_reviews', 'implementation_analysis', 'unknowns', 'tech_stack', 'selected_area', 'summary'):
            if key in result:
                del result[key]
                break
        else:
            return {}
    return result
