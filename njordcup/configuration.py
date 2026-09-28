"""Trusted user configuration; target repositories are never searched for settings."""
import argparse
import os
from pathlib import Path
import sys


SETTINGS = set('''model base_url api_key_env output_mode max_calls workers
request_timeout max_retries retry_base retry_max_delay max_seconds max_tokens
context_window bytes_per_token token_margin max_input_chars batch_chars context_chars
max_file_bytes context_rounds index_mode cache log_file trace_file quick_max_files'''.split())

ADVANCED = SETTINGS - {'model', 'workers', 'max_calls', 'max_seconds', 'quick_max_files'}


def parse_configuration(parser, argv):
    argv = list(sys.argv[1:] if argv is None else argv)
    bootstrap = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    bootstrap.add_argument('--config', type=Path)
    bootstrap.add_argument('--no-config', action='store_true')
    bootstrap.add_argument('--profile')
    options, _ = bootstrap.parse_known_args(argv)
    if '--help-all' in argv:
        parser.print_help()
        parser.exit()
    for action in parser._actions:
        if action.dest in ADVANCED:
            action.help = argparse.SUPPRESS
    # Help must work even if a saved configuration is broken.
    if '--help' in argv or '-h' in argv:
        return parser.parse_args(argv)
    if options.no_config and (options.config or options.profile):
        parser.error('--no-config cannot be combined with --config or --profile')
    if not options.no_config:
        config_root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        if not config_root.is_absolute():
            config_root = Path.home() / '.config'
        path = options.config or config_root / 'njordcup' / 'config.toml'
        if options.config or path.exists():
            load_defaults(parser, path.expanduser(), options.profile)
        elif options.profile:
            parser.error('A profile requires an existing configuration file')
    return parser.parse_args(argv)


def load_defaults(parser, path, profile):
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with path.open('rb') as stream:
            data = tomllib.load(stream)
    except ImportError:
        parser.error('TOML configuration on Python 3.10 requires tomli; install njordcup or pip install tomli')
    except (OSError, ValueError) as exc:
        parser.error(f'Cannot read configuration {path}: {exc}')
    if set(data) - {'settings', 'profiles', 'default_profile'}:
        parser.error('Unknown configuration sections: ' + ', '.join(sorted(set(data) - {'settings', 'profiles', 'default_profile'})))
    actions = {a.dest: a for a in parser._actions if a.dest in SETTINGS}

    def validate(values, section):
        if not isinstance(values, dict):
            parser.error(f'{section} must be a TOML table')
        result = {}
        for key, value in values.items():
            if key not in actions:
                parser.error(f'Unknown configuration setting {section}.{key}')
            action = actions[key]
            try:
                if action.type is None or action.type is Path:
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError('must be a nonempty string')
                elif isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError('must be a number')
                # String conversion preserves integer validation (no float truncation).
                converted = action.type(str(value)) if action.type else value
                if action.choices and converted not in action.choices:
                    raise ValueError('must be one of ' + ', '.join(action.choices))
                if isinstance(converted, Path):
                    converted = converted.expanduser()
                    if not converted.is_absolute():
                        converted = path.resolve().parent / converted
                result[key] = converted
            except (ValueError, TypeError, argparse.ArgumentTypeError) as exc:
                parser.error(f'Invalid configuration setting {section}.{key}: {exc}')
        return result

    defaults = validate(data.get('settings', {}), 'settings')
    profiles = data.get('profiles', {})
    if not isinstance(profiles, dict):
        parser.error('profiles must be a TOML table')
    profiles = {name: validate(values, f'profiles.{name}') for name, values in profiles.items()}
    default_profile = data.get('default_profile')
    if default_profile is not None and (not isinstance(default_profile, str) or default_profile not in profiles):
        parser.error('default_profile must name an existing profile')
    selected = profile if profile is not None else default_profile
    if selected is not None:
        if selected not in profiles:
            parser.error(f'Unknown configuration profile: {selected}')
        defaults.update(profiles[selected])
    parser.set_defaults(**defaults)
