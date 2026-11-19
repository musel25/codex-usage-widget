"""Read-only Codex usage access and account-scoped normalized caching."""
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
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
    account_key: str | None = field(default=None, compare=False, repr=False)


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
        return replace(parse_usage(payload), account_key=hashlib.sha256(auth["account_id"].encode()).hexdigest())
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


def current_account_key():
    return hashlib.sha256(load_auth()['account_id'].encode()).hexdigest()


def _cache_path(account_key=None):
    digest = account_key or current_account_key()
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
        path = _cache_path()
        return replace(_cached_usage(json.loads(path.read_text())), account_key=path.stem)
    except (UsageError, OSError, ValueError, TypeError, KeyError):
        return None


def save_cached_usage(usage) -> None:
    """Atomically replace a private cache with normalized data only."""
    temporary = None
    try:
        raw = asdict(usage)
        raw.pop('account_key', None)
        data = asdict(_cached_usage(raw))
        data.pop('account_key', None)
        path = _cache_path(usage.account_key)
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


def _pass_timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def parse_reset_passes(payload, now=None):
    """Keep only available, unexpired reset credits; omit credit IDs."""
    current = _pass_timestamp(now) if isinstance(now, str) else datetime.now(timezone.utc)
    if current is None or not isinstance(payload, dict) or not isinstance(payload.get('credits'), list):
        return []
    passes = []
    for credit in payload['credits']:
        if not isinstance(credit, dict) or credit.get('status') != 'available':
            continue
        expiry = _pass_timestamp(credit.get('expires_at'))
        label = credit.get('title')
        if expiry and expiry > current and isinstance(label, str) and label.strip():
            passes.append({'label': label.strip(), 'expires_at': credit['expires_at']})
    return sorted(passes, key=lambda item: item['expires_at'])


def format_pass_expiry(expires_at, now=None):
    expiry = _pass_timestamp(expires_at)
    if expiry is None:
        return 'Expiry unavailable'
    current = now or datetime.now(timezone.utc)
    local = expiry.astimezone()
    remaining = max(0, int((expiry - current).total_seconds()))
    days, remainder = divmod(remaining, 86400)
    hours = remainder // 3600
    left = f'{days}d {hours}h left' if days else f'{hours}h left' if hours else 'Less than 1h left'
    return f"Expires {local.strftime('%b')} {local.day} at {local.strftime('%I:%M %p %Z').lstrip('0')} · {left}"


def fetch_reset_passes(expected_account_key=None):
    """Read the banked reset list; never redeem a credit."""
    auth = load_auth()
    account_key = hashlib.sha256(auth['account_id'].encode()).hexdigest()
    if expected_account_key is not None and account_key != expected_account_key:
        raise UsageError('Codex account changed. Refresh usage.')
    request = urllib.request.Request(
        'https://chatgpt.com/backend-api/wham/rate-limit-reset-credits',
        headers={'Authorization': 'Bearer ' + auth['access_token'],
                 'ChatGPT-Account-Id': auth['account_id'],
                 'Accept': 'application/json'}, method='GET')
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
            payload = json.load(response)
        if current_account_key() != account_key:
            raise UsageError('Codex account changed. Refresh usage.')
        if not isinstance(payload, dict) or not isinstance(payload.get('credits'), list):
            raise ValueError
        return parse_reset_passes(payload)
    except Exception:
        raise UsageError('Limit reset passes unavailable') from None


def _pass_cache_path(account_key=None):
    return _cache_path(account_key).with_suffix('.resets.json')


def load_cached_passes():
    """Load this account's saved pass list; drop expired entries."""
    try:
        data = json.loads(_pass_cache_path().read_text())
        if not isinstance(data, list):
            return None
        now = datetime.now(timezone.utc)
        return [item for item in data if isinstance(item, dict)
                and set(item) == {'label', 'expires_at'}
                and isinstance(item['label'], str) and item['label'].strip()
                and (expiry := _pass_timestamp(item['expires_at'])) and expiry > now]
    except (UsageError, OSError, ValueError, TypeError):
        return None


def save_cached_passes(passes, account_key=None):
    """Save normalized pass labels and expiries, without tokens or credit IDs."""
    path = _pass_cache_path(account_key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix='.resets-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(passes, stream, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
