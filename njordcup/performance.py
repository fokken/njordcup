"""Controller measurements, not model-server decoding benchmarks."""
from copy import deepcopy

COUNTERS = ('requests', 'attempts', 'cache_hits', 'retries', 'input_tokens', 'output_tokens',
            'elapsed_seconds', 'http_seconds', 'context_overflows', 'failures', 'responses_with_usage', 'responses_without_usage')


def snapshot(provider):
    return deepcopy(getattr(provider, 'performance', {}))


def summarize(current, previous=None):
    phases = {}
    previous = previous or {}
    for name, values in current.items():
        row = {k: max(0, values.get(k, 0) - previous.get(name, {}).get(k, 0)) for k in COUNTERS}
        if not row['requests']:
            continue
        row['average_attempt_seconds'] = row['http_seconds'] / row['attempts'] if row['attempts'] else None
        row['output_tokens_per_second'] = (row['output_tokens'] / row['elapsed_seconds']
                                           if row['responses_with_usage'] and row['elapsed_seconds'] else None)
        phases[name] = row
    return {'phases': phases, 'note': 'Phase time includes input processing, retries and request overhead. Output throughput is end-to-end, not decoding speed; token counts depend on server usage metadata.'}


def log_performance(log, provider):
    for name, row in summarize(snapshot(provider))['phases'].items():
        throughput = f"{row['output_tokens_per_second']:.1f}" if row['output_tokens_per_second'] is not None else 'unavailable'
        average = f"{row['average_attempt_seconds']:.1f}s" if row['average_attempt_seconds'] is not None else 'unavailable'
        log.info('Performance %s: %.1fs, %d API attempts, %d cache hits, average attempt %s, '
                 '%d input / %d output tokens, end-to-end output tokens/s %s',
                 name, row['elapsed_seconds'], row['attempts'], row['cache_hits'], average,
                 row['input_tokens'], row['output_tokens'], throughput)
