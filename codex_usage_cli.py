"""Command-line Codex usage display."""
import argparse
from dataclasses import asdict
import json
import sys

from codex_usage_core import UsageError, fetch_usage, fetch_reset_passes, format_pass_expiry, reset_text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Show current Codex usage.')
    parser.add_argument('--json', action='store_true', help='Print normalized usage as JSON')
    args = parser.parse_args(argv)
    try:
        usage = fetch_usage()
    except UsageError as error:
        print(f'Codex usage: {error}', file=sys.stderr)
        return 1
    try:
        passes = fetch_reset_passes(usage.account_key)
    except UsageError:
        passes = None
    if args.json:
        data = asdict(usage)
        data.pop('account_key', None)
        data['limit_reset_passes'] = passes
        print(json.dumps(data, allow_nan=False))
    else:
        print(f'Codex usage · {usage.plan}')
        for label, window in (('Weekly', usage.weekly), ('Session', usage.session)):
            if window is None:
                print(f'{label}: unavailable')
            else:
                print(f'{label}: {window.used_percent:g}% used '
                      f'({100 - window.used_percent:g}% remaining) · '
                      f'reset: {reset_text(window.reset_at)}')
        print('Available limit resets:')
        if passes is None:
            print('  Pass data unavailable')
        elif not passes:
            print('  No passes available')
        else:
            for item in passes:
                print(f'  {item["label"]} · {format_pass_expiry(item["expires_at"])}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
