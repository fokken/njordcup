"""Resumable, bounded prose synthesis of saved reports; never changes findings."""
import hashlib
import json
import logging
from datetime import datetime, timezone

from .errors import ReviewError, RunStopped
from .flyover import provider_identity
from .narrative import Narrative

log = logging.getLogger(__name__)
SCHEMA = {'type': 'object', 'properties': {'report_synthesis': {'type': 'string'}}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def report_input(report):
    return {k: v for k, v in report.items() if k not in {'report_synthesis', 'memory_path'}}


def saved_synthesis(report, saved):
    state = saved.get('report_synthesis', {})
    return state if state.get('input_hash') == digest(report_input(report)) else None


def synthesize(report, saved, provider, checkpoint):
    """Checkpoint each map/reduce response in the source artifact, separate from analyses."""
    data = report_input(report)
    identity = {'provider': provider_identity(provider), 'max_tokens': provider.max_tokens,
                'max_input_chars': provider.max_input_chars, 'context_window': provider.context_window,
                'bytes_per_token': provider.bytes_per_token, 'token_margin': provider.token_margin}
    state = saved_synthesis(report, saved)
    if not state or state.get('settings') != identity or state.get('version') != 2:
        state = {'version': 2, 'input_hash': digest(data), 'settings': identity, 'nodes': {}, 'status': 'incomplete'}
    saved['report_synthesis'] = state
    if state['status'] == 'complete':
        log.info('Reusing saved report synthesis')
        return state
    kind = 'implementation' if report.get('kind') == 'implementation_analysis' else 'security'

    def payload(parts, stage):
        return {'report_kind': kind, 'stage': stage, 'saved_analysis_fragments': parts}

    def fits(parts, stage):
        return provider.fits('', payload(parts, stage), SCHEMA)

    def ask(parts, stage):
        provider.control.check()
        key = digest([stage, parts])
        previous = state['nodes'].get(key)
        if previous and previous['complete']:
            return previous['text']
        log.info('Synthesizing report: %s (%d completed responses)', stage,
                 sum(n['complete'] for n in state['nodes'].values()))
        response = provider.ask('', payload(parts, stage), SCHEMA)
        if not isinstance(response, Narrative):
            raise ReviewError('Report synthesis requires a prose response')
        state['nodes'][key] = response.record()
        checkpoint()
        if not response.complete:
            raise ReviewError('Report synthesis response was empty or unfinished; increase --max-tokens or retry')
        return str(response)

    def split(text):
        provider.control.check()
        if fits([text], 'summarize'):
            return [text]
        if len(text) < 2:
            raise ReviewError('Report synthesis instructions exceed the input budget')
        middle = len(text) // 2
        return split(text[:middle]) + split(text[middle:])

    try:
        fragments = split(json.dumps(data, ensure_ascii=True, sort_keys=True))
        if len(fragments) == 1:
            result = ask(fragments, 'summarize')
        else:
            summaries = [ask([part], 'summarize') for part in fragments]
            while len(summaries) > 1:
                reduced = []
                for i in range(0, len(summaries), 2):
                    pair = summaries[i:i + 2]
                    if len(pair) == 1:
                        reduced.extend(pair)
                    else:
                        if not fits(pair, 'combine'):
                            raise ReviewError('Intermediate summaries exceed synthesis input budget; increase input/context limits or reduce --max-tokens')
                        reduced.append(ask(pair, 'combine'))
                summaries = reduced
            result = summaries[0]
        state.update(status='complete', text=result, saved_at=datetime.now(timezone.utc).isoformat())
        state.pop('error', None)
        checkpoint()
    except RunStopped:
        checkpoint()
        raise
    except ReviewError as exc:
        state.update(status='incomplete', error=str(exc))
        checkpoint()
        log.warning('Report synthesis incomplete: %s', exc)
    return state
