"""Offline contract tests; all credentials are synthetic."""
import contextlib
import importlib
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request


def payload(window=None, secondary=None):
    return {"plan_type": "prolite", "rate_limit": {
        "allowed": True, "limit_reached": False,
        "primary_window": window, "secondary_window": secondary},
        "code_review_rate_limit": None, "additional_rate_limits": None}


def window(**changes):
    return dict(used_percent=51, limit_window_seconds=604800,
                reset_at=1790210893, reset_after_seconds=459253, **changes)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('codex_usage_core'),
                             'core implementation is missing')
        self.core = importlib.import_module('codex_usage_core')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'auth'),
                                     'XDG_CACHE_HOME': str(self.root / 'cache')})
        env.start()
        self.addCleanup(env.stop)
        (self.root / 'auth').mkdir()
        self.auth()

    def auth(self, account='fake-account', token='fake-token'):
        (self.root / 'auth/auth.json').write_text(json.dumps({
            'tokens': {'access_token': token, 'account_id': account,
                       'refresh_token': 'fake-refresh-secret'}}))

    def test_weekly_in_either_position_and_optional_session(self):
        session = {'used_percent': 12, 'limit_window_seconds': 18000}
        for primary, secondary in [(window(), session), (session, window())]:
            usage = self.core.parse_usage(payload(primary, secondary), now=1000)
            self.assertEqual((usage.plan, usage.weekly.used_percent,
                              usage.weekly.reset_at, usage.session.duration,
                              usage.fetched_at), ('prolite', 51, 1790210893, 18000, 1000))

    def test_null_missing_and_malformed_windows_never_become_zero(self):
        invalid = [None, {}, [], 'bad', {'used_percent': 51}]
        for key in ('used_percent', 'limit_window_seconds', 'reset_at'):
            for value in (None, True, '51', float('nan'), float('inf'), -1):
                if key == 'reset_at' and value is None:
                    continue
                invalid.append({**window(), key: value})
        invalid.append({**window(), 'used_percent': 101})
        for bad in invalid:
            with self.subTest(bad=bad):
                self.assertIsNone(self.core.parse_usage(payload(bad), now=1000).weekly)
        for data in ({}, {'rate_limit': None}, payload(), payload({'used_percent': 2, 'limit_window_seconds': 18000})):
            self.assertIsNone(self.core.parse_usage(data).weekly)

    def test_reset_relative_resolved_once_and_missing_allowed(self):
        data = {'used_percent': 0, 'limit_window_seconds': 604800,
                'reset_after_seconds': 60}
        usage = self.core.parse_usage(payload(data), now=1000)
        self.assertEqual(usage.weekly.reset_at, 1060)
        del data['reset_after_seconds']
        self.assertIsNone(self.core.parse_usage(payload(data), now=1000).weekly.reset_at)
        for bad in (True, float('nan'), -1, '60'):
            self.assertIsNone(self.core.parse_usage(payload({**data, 'reset_after_seconds': bad})).weekly)

    def test_countdown(self):
        for reset, expected in [(None, 'Unknown'), (999, 'Due now'),
                                (1000, 'Due now'), (1001, '1m'),
                                (4660, '1h 1m'), (177460, '2d 1h')]:
            self.assertEqual(self.core.reset_text(reset, now=1000), expected)

    def test_safe_auth_errors_and_fresh_credentials(self):
        self.assertEqual(self.core.load_auth(), {'access_token': 'fake-token', 'account_id': 'fake-account'})
        for value in ('fake-token INVALID JSON', '{}', 'null', '{"tokens":{"access_token":true,"account_id":"x"}}'):
            (self.root / 'auth/auth.json').write_text(value)
            with self.assertRaises(self.core.UsageError) as error:
                self.core.load_auth()
            self.assertIn('codex login', str(error.exception))
            self.assertNotIn('fake-token', str(error.exception))
        (self.root / 'auth/auth.json').unlink()
        with self.assertRaises(self.core.UsageError):
            self.core.load_auth()

    def test_default_auth_home(self):
        (self.root / '.codex').mkdir()
        (self.root / '.codex/auth.json').write_text('{"tokens":{"access_token":"synthetic","account_id":"a"}}')
        with patch.dict(os.environ, {'HOME': str(self.root)}):
            del os.environ['CODEX_HOME']
            self.assertEqual(self.core.load_auth()['account_id'], 'a')

    def test_cache_round_trip_permissions_corruption_and_account_isolation(self):
        usage = self.core.parse_usage(payload(window()), now=1000)
        self.core.save_cached_usage(usage)
        self.assertEqual(self.core.load_cached_usage(), usage)
        files = list((self.root / 'cache/codex-usage-widget').iterdir())
        self.assertEqual(len(files), 1)
        cache = files[0]
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual(cache.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('fake-account', cache.name)
        for secret in ('fake-account', 'fake-token', 'fake-refresh-secret'):
            self.assertNotIn(secret, cache.read_text())
        self.auth(account='second-account')
        self.assertIsNone(self.core.load_cached_usage())
        self.auth()
        original = json.loads(cache.read_text())
        for broken in ('{broken', 'null', '{}', json.dumps({**original, 'fetched_at': True}),
                       json.dumps({**original, 'weekly': {'used_percent': float('nan')}})):
            cache.write_text(broken)
            self.assertIsNone(self.core.load_cached_usage())

    def test_fetch_request_contract_and_auth_reread(self):
        seen = []
        def open_request(opener, request, timeout):
            seen.append((request, timeout))
            return contextlib.closing(io.BytesIO(json.dumps(payload(window())).encode()))
        with patch.object(urllib.request.OpenerDirector, 'open', open_request):
            self.assertEqual(self.core.fetch_usage().weekly.used_percent, 51)
            self.auth(token='changed-token')
            self.core.fetch_usage()
        for request, timeout in seen:
            self.assertEqual(request.full_url, 'https://chatgpt.com/backend-api/wham/usage')
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(timeout, 15)
            self.assertEqual(request.get_header('Chatgpt-account-id'), 'fake-account')
            self.assertIsNone(request.get_header('Cookie'))
        self.assertEqual(seen[1][0].get_header('Authorization'), 'Bearer changed-token')

    def test_http_and_network_errors_are_safe(self):
        for code, phrase in [(401, 'codex login'), (403, 'access'), (429, 'later'), (500, 'service')]:
            failure = urllib.error.HTTPError('https://example.test', code, 'fake-token', {}, io.BytesIO(b'fake-token'))
            with patch.object(urllib.request.OpenerDirector, 'open', side_effect=failure):
                with self.assertRaises(self.core.UsageError) as error:
                    self.core.fetch_usage()
                self.assertIn(phrase, str(error.exception).lower())
                self.assertNotIn('fake-token', str(error.exception))
        for failure in (urllib.error.URLError('fake-token'), TimeoutError('fake-token')):
            with patch.object(urllib.request.OpenerDirector, 'open', side_effect=failure):
                with self.assertRaises(self.core.UsageError) as error:
                    self.core.fetch_usage()
                self.assertNotIn('fake-token', str(error.exception))

    def test_redirect_cannot_send_credentials_to_another_host(self):
        calls = []
        def https_open(handler, request):
            calls.append(request.full_url)
            response = urllib.response.addinfourl(io.BytesIO(b''),
                {'Location': 'https://attacker.invalid/steal'}, request.full_url, 302)
            response.msg = 'Found'
            return response
        with patch.object(urllib.request.HTTPSHandler, 'https_open', https_open):
            with self.assertRaises(self.core.UsageError):
                self.core.fetch_usage()
        self.assertEqual(calls, ['https://chatgpt.com/backend-api/wham/usage'])

    def test_interrupted_and_invalid_responses_are_safe(self):
        for failure in (http.client.IncompleteRead(b'fake-token'),
                        http.client.BadStatusLine('fake-token')):
            with patch.object(urllib.request.OpenerDirector, 'open', side_effect=failure):
                with self.assertRaises(self.core.UsageError) as error:
                    self.core.fetch_usage()
                self.assertNotIn('fake-token', str(error.exception))
        for body in (b'fake-token', b'null', b'[]', b'\xff'):
            with patch.object(urllib.request.OpenerDirector, 'open',
                              return_value=contextlib.closing(io.BytesIO(body))):
                with self.assertRaises(self.core.UsageError) as error:
                    self.core.fetch_usage()
                self.assertNotIn('fake-token', str(error.exception))

    def test_failed_atomic_replacement_preserves_previous_sample(self):
        previous = self.core.parse_usage(payload(window()), now=1000)
        self.core.save_cached_usage(previous)
        replacement = self.core.parse_usage(payload(window()), now=2000)
        with patch.object(os, 'replace', side_effect=OSError('fake-token')):
            with self.assertRaises(self.core.UsageError) as error:
                self.core.save_cached_usage(replacement)
        self.assertNotIn('fake-token', str(error.exception))
        self.assertEqual(self.core.load_cached_usage(), previous)
        self.assertEqual(len(list((self.root / 'cache/codex-usage-widget').iterdir())), 1)

    def test_cli_json_summary_and_error_exit(self):
        self.assertIsNotNone(importlib.util.find_spec('codex_usage_cli'))
        cli = importlib.import_module('codex_usage_cli')
        usage = self.core.parse_usage(payload(window()), now=1000)
        for args in (['--json'], []):
            output = io.StringIO()
            with patch.object(cli, 'fetch_usage', return_value=usage), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(args), 0)
            if args:
                self.assertEqual(json.loads(output.getvalue())['weekly']['used_percent'], 51)
            else:
                self.assertIn('51%', output.getvalue())
                self.assertIn('prolite', output.getvalue())
        error_output = io.StringIO()
        with patch.object(cli, 'fetch_usage', side_effect=self.core.UsageError('Run codex login.')), contextlib.redirect_stderr(error_output):
            self.assertNotEqual(cli.main([]), 0)
        self.assertIn('codex login', error_output.getvalue())


if __name__ == '__main__':
    unittest.main()

class TimestampTests(unittest.TestCase):
    def test_unrepresentable_reset_is_unavailable(self):
        import codex_usage_core as core
        for field in ("reset_at", "reset_after_seconds"):
            payload = {"rate_limit": {"primary_window": {"used_percent": 20, "limit_window_seconds": 604800, field: 1e100}}}
            self.assertIsNone(core.parse_usage(payload, now=100).weekly)
