"""Conservative recognition of explicit provider context-limit errors."""
import json
import re

CODES = {'context_length_exceeded', 'context_window_exceeded', 'exceed_context_size_error',
         'max_context_length_exceeded', 'input_too_long'}


def is_context_overflow(status, raw):
    if status not in {400, 413, 422}:
        return False
    text = raw[:65536].decode('utf-8', errors='replace')
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get('error', data)
        if isinstance(error, dict):
            if any(isinstance(error.get(k), str) and error[k] in CODES for k in ('code', 'type')):
                return True
            text = str(error.get('message', ''))
        elif isinstance(error, str):
            text = error
        else:
            return False
    # Do not treat generic 400/413 responses, OOM, or invalid output parameters as
    # token overflow. Require both context vocabulary and an explicit excess.
    text = text.lower()
    return bool(re.search(r'(context (?:length|window|size)|max_model_len|maximum model length)', text)
                and re.search(r'(exceed|too (?:long|large)|longer than|reduce the length|requested \d+ tokens)', text))
