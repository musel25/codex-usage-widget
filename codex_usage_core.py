"""Read-only Codex usage access and account-scoped normalized caching."""
from dataclasses import asdict, dataclass
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request


class UsageError(Exception):
    """A safe, actionable error suitable for display to the user."""


@dataclass(frozen=True)
class Window:
    used_percent: float
    reset_at: float | None
    duration: int


@dataclass(frozen=True)
class Usage:
    plan: str
    weekly: Window | None
    session: Window | None
    fetched_at: float


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def load_auth() -> dict:
    """Read fresh credentials without modifying the shared login."""
    try:
        home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        data = json.loads((home / 'auth.json').read_text())
        tokens = data['tokens']
        result = {key: tokens[key] for key in ('access_token', 'account_id')}
        if any(not isinstance(value, str) or not value.strip()
               or any(ord(char) < 33 or ord(char) > 126 for char in value)
               for value in result.values()):
            raise ValueError
        return result
    except (OSError, ValueError, TypeError, KeyError):
        raise UsageError('Unable to read Codex login. Run codex login and try again.') from None


def _window(data, now):
    if not isinstance(data, dict):
        return None
    duration = data.get('limit_window_seconds')
    used = data.get('used_percent')
    if type(duration) is not int or duration not in (604800, 18000):
        return None
    if not _number(used) or used > 100:
        return None
    reset = data.get('reset_at')
    if reset is not None:
        if not _number(reset):
            return None
    elif data.get('reset_after_seconds') is not None:
        after = data['reset_after_seconds']
        if not _number(after):
            return None
        reset = now + after
        if not _number(reset):
            return None
    if reset is not None and reset > 253402214400:
        return None  # Keep reset dates representable by the desktop date formatter.
    return Window(float(used), float(reset) if reset is not None else None, duration)


def parse_usage(payload, now=None) -> Usage:
    """Normalize known windows; malformed or absent limits stay unavailable."""
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or not _number(now):
        raise UsageError('The usage service returned an invalid response.')
    plan = payload.get('plan_type')
    if not isinstance(plan, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', plan):
        plan = 'Unknown'
    limits = payload.get('rate_limit')
    limits = limits if isinstance(limits, dict) else {}
    windows = [_window(limits.get(key), now)
               for key in ('primary_window', 'secondary_window')]
    weekly = next((w for w in windows if w and w.duration == 604800), None)
    session = next((w for w in windows if w and w.duration == 18000), None)
    return Usage(plan, weekly, session, float(now))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UsageError('The usage service redirected the request; redirect blocked.')


def fetch_usage() -> Usage:
    """GET only the usage endpoint; never refresh tokens or follow redirects."""
    auth = load_auth()
    request = urllib.request.Request(
        'https://chatgpt.com/backend-api/wham/usage',
        headers={'Authorization': 'Bearer ' + auth['access_token'],
                 'ChatGPT-Account-Id': auth['account_id'],
                 'Accept': 'application/json'}, method='GET')
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
            payload = json.load(response)
        return parse_usage(payload)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        message = {
            401: 'Codex login expired. Run codex login and try again.',
            403: 'This account cannot access Codex usage. Check your account access.',
            429: 'Too many usage requests. Retry later.',
        }.get(code, 'The usage service is unavailable. Try again later.')
        raise UsageError(message) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise UsageError('Unable to reach the usage service. Check your connection and try again.') from None
    except (ValueError, UnicodeError):
        raise UsageError('The usage service returned an invalid response.') from None


def _cache_path():
    account = load_auth()['account_id']
    digest = hashlib.sha256(account.encode()).hexdigest()
    base = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return base / 'codex-usage-widget' / (digest + '.json')


def _cached_usage(data):
    if not isinstance(data, dict) or set(data) != {'plan', 'weekly', 'session', 'fetched_at'}:
        raise ValueError
    if not _number(data['fetched_at']):
        raise ValueError
    if not isinstance(data['plan'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', data['plan']):
        raise ValueError
    windows = []
    for key, duration in (('weekly', 604800), ('session', 18000)):
        raw = data[key]
        if raw is None:
            windows.append(None)
            continue
        if not isinstance(raw, dict) or set(raw) != {'used_percent', 'reset_at', 'duration'}:
            raise ValueError
        parsed = _window({'used_percent': raw['used_percent'],
                          'reset_at': raw['reset_at'],
                          'limit_window_seconds': raw['duration']}, data['fetched_at'])
        if parsed is None or parsed.duration != duration:
            raise ValueError
        windows.append(parsed)
    return Usage(data['plan'], *windows, float(data['fetched_at']))


def load_cached_usage() -> Usage | None:
    """Return the account's last sample, including its original timestamp.

    Consumers decide when to display a stale label. Cache misses and damaged
    files never masquerade as fresh data or prevent a network request.
    """
    try:
        return _cached_usage(json.loads(_cache_path().read_text()))
    except (UsageError, OSError, ValueError, TypeError, KeyError):
        return None


def save_cached_usage(usage) -> None:
    """Atomically replace a private cache with normalized data only."""
    temporary = None
    try:
        data = asdict(_cached_usage(asdict(usage)))
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        fd, temporary = tempfile.mkstemp(prefix='.usage-', dir=path.parent)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError, KeyError):
        raise UsageError('Unable to save the usage cache.') from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def reset_text(reset_at, now=None) -> str:
    """Human countdown, rounding up so an upcoming reset is not shown as due."""
    if reset_at is None or not _number(reset_at):
        return 'Unknown'
    remaining = reset_at - (time.time() if now is None else now)
    if remaining <= 0:
        return 'Due now'
    minutes = math.ceil(remaining / 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'
