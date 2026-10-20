import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import recheck_watch as watch
from install_recheck_watch import install

CONFIG = {'hash': 'a' * 40, 'container': 'synthetic-app', 'save_path': '/downloads/media/nas-synthetic/files/library',
          'size': 42, 'file_count': 2, 'probe_file': 'Synthetic/E01.mkv', 'max_retries': 5, 'hours': 72}


def observation(state='checkingDL', progress=.2, completed=10, left=32):
    return {'row': {'name': 'Synthetic', 'state': state, 'progress': progress, 'completed': completed,
                    'downloaded': 0, 'amount_left': left, 'size': 42}}


class WatchTests(unittest.TestCase):
    def test_active_slow_check_never_restarts(self):
        state = watch.initial(0)
        for now in [0, 10, 2000, 4000]:
            self.assertIsNone(watch.evaluate(state, observation(), CONFIG, now))
        self.assertEqual(state['status'], 'stalled_check')
        self.assertEqual(state['retries'], 0)
        self.assertIsNone(watch.evaluate(state, observation(progress=.3, completed=15), CONFIG, 4100))
        self.assertEqual(state['status'], 'checking')

    def test_error_waits_and_reserves_at_most_five_retries(self):
        state = watch.initial(0)
        error = observation('error')
        self.assertIsNone(watch.evaluate(state, error, CONFIG, 0))
        for attempt in range(5):
            when = state['retry_at']
            self.assertIsNone(watch.evaluate(state, error, CONFIG, when - 1))
            self.assertEqual(watch.evaluate(state, error, CONFIG, when), 'recover')
            self.assertEqual(state['retries'], attempt + 1)
        self.assertIsNone(watch.evaluate(state, error, CONFIG, state['retry_at']))
        self.assertEqual(state['status'], 'exhausted')

    def test_restart_cancellation_and_exhaustion_preserve_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            now = [0]
            calls = []
            def invoke(config, action):
                calls.append(action)
                if action == 'observe':
                    return observation('error')
                # Simulate interruption immediately after reservation.
                raise KeyboardInterrupt()
            watch.tick(CONFIG, path, invoke, lambda: now[0])
            now[0] = 120
            with self.assertRaises(KeyboardInterrupt):
                watch.tick(CONFIG, path, invoke, lambda: now[0])
            saved = json.loads(path.read_text())
            self.assertEqual(saved['retries'], 1)
            watch.tick(CONFIG, path, invoke, lambda: now[0])
            self.assertEqual(calls.count('recover'), 1)
            saved['retries'] = 5
            watch.atomic(path, saved)
            state = watch.tick(CONFIG, path, lambda c, a: observation('error'), lambda: 10000)
            self.assertEqual(state['status'], 'exhausted')
            def forbidden(*args):
                self.fail('A terminal watch must make no more remote calls')
            watch.tick(CONFIG, path, forbidden, lambda: 10001)

    def test_complete_is_verified_and_never_started(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def invoke(config, action):
                calls.append(action)
                return observation('stoppedUP', 1, 42, 0) if action == 'observe' else {'verified': True, 'state': 'stoppedUP'}
            state = watch.tick(CONFIG, Path(folder) / 'state.json', invoke, lambda: 0)
            self.assertEqual(state['status'], 'verified_paused')
            self.assertEqual(calls, ['observe', 'verify'])

    def test_incomplete_or_manual_change_and_missing_are_terminal(self):
        for observed, status in [(observation('stoppedDL'), 'incomplete'),
                                 (observation('downloading'), 'manual_change'),
                                 ({'manualChange': True}, 'manual_change'), ({'missing': True}, 'missing')]:
            with self.subTest(status=status):
                state = {**watch.initial(0), 'checking_seen': True}
                self.assertIsNone(watch.evaluate(state, observed, CONFIG, 1))
                self.assertEqual(state['status'], status)

    def test_api_outage_does_not_consume_recovery_budget_or_erase_progress(self):
        state = watch.initial(0)
        watch.evaluate(state, observation(), CONFIG, 1)
        watch.evaluate(state, {'errorType': 'TimeoutError'}, CONFIG, 2)
        self.assertEqual(state['retries'], 0)
        self.assertEqual(state['completed'], 10)
        self.assertEqual(state['status'], 'unavailable')

    def test_expiry_is_bounded(self):
        state = watch.initial(0)
        self.assertIsNone(watch.evaluate(state, observation(), CONFIG, 72 * 3600))
        self.assertEqual(state['status'], 'expired')

    def test_recovery_failure_is_logged_without_resetting_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            state = {**watch.initial(0), 'retry_at': 0}
            watch.atomic(path, state)
            def invoke(config, action):
                return observation('error') if action == 'observe' else {'errorType': 'OSError', 'errno': 5}
            result = watch.tick(CONFIG, path, invoke, lambda: 1)
            self.assertEqual(result['retries'], 1)
            self.assertEqual(result['status'], 'retry_wait')
            self.assertIn('OSError', path.with_suffix('.jsonl').read_text())

    def test_file_diagnostics_are_scoped_and_redacted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'qbittorrent.log'
            path.write_text('File error alert. Torrent: "Synthetic". File: "E01". I/O error https://host/path?secret=private token=secret\n'
                            'File error alert. Torrent: "Other". do not include\n')
            lines = watch.error_report({'client_log_dir': folder}, 'Synthetic')
            self.assertEqual(len(lines), 1)
            self.assertNotIn('secret', lines[0])
            self.assertNotIn('Other', lines[0])

    def test_fresh_install_and_upgrade_preserve_configuration_ledger_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);destination = root / 'watch';units = root / 'units';calls = []
            runner = lambda args, **kwargs: calls.append(args)
            install(CONFIG, destination, units, Path(watch.__file__), runner)
            self.assertEqual(json.loads((destination / 'config.json').read_text()), CONFIG)
            state = {**watch.initial(0), 'retries': 3}
            watch.atomic(destination / 'state.json', state)
            credential = root / 'existing-credential';credential.write_text('private-do-not-touch')
            install(CONFIG, destination, units, Path(watch.__file__), runner)
            self.assertEqual(json.loads((destination / 'state.json').read_text())['retries'], 3)
            self.assertEqual(credential.read_text(), 'private-do-not-touch')
            self.assertNotIn('restart', json.dumps(calls))
            self.assertIn('OnUnitInactiveSec=60s', (units / 'watch.timer').read_text())
            with self.assertRaises(ValueError):
                install({**CONFIG, 'hash': 'b' * 40}, destination, units, Path(watch.__file__), runner)
            self.assertEqual(json.loads((destination / 'state.json').read_text())['retries'], 3)


    def test_actual_worker_rechecks_only_after_storage_vpn_and_stop_confirmation(self):
        import base64
        import contextlib
        import io
        import os
        import types
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);probe = root / 'Synthetic/E01.mkv'
            probe.parent.mkdir();probe.write_bytes(b'x' * 8192)
            token = root / 'token';token.write_text('synthetic-private-key')
            config = {**CONFIG, 'save_path': folder, 'action': 'recover'}
            requests = []
            row = {'hash': CONFIG['hash'], 'save_path': folder, 'content_path': folder,
                   'size': CONFIG['size'], 'state': 'error'}
            ready = [True]
            fail_stop = [False]
            def urlopen(request, **kwargs):
                if request.full_url.endswith('status'):
                    return io.BytesIO(json.dumps({'ready': ready[0], 'torrentCapabilities': ['recheck']}).encode())
                body = json.loads(request.data)
                requests.append(body)
                if body['path'] == 'torrents/info':
                    result = [row]
                elif body['path'] == 'torrents/stop':
                    if fail_stop[0]:raise OSError()
                    row['state'] = 'stoppedDL';result = None
                elif body['path'] == 'torrents/recheck':
                    result = None
                else:
                    self.fail('Unexpected RPC: ' + body['path'])
                return io.BytesIO(json.dumps({'status': 200, 'body': base64.b64encode(json.dumps(result).encode()).decode()}).encode())
            storage = types.ModuleType('mediaseed.storage')
            class Storage:
                def __init__(self, *args):pass
                def describe(self, path):return {'location': 'synthetic', 'folder': ''}
                def directory(self, *args):pass
            storage.Storage = Storage
            def run():
                output = io.StringIO()
                env = {'MEDIASEED_VPN_TOKEN_FILE': str(token), 'MEDIASEED_VPN_URL': 'http://synthetic-internal'}
                with patch.dict(os.environ, env), patch.dict(sys.modules, {'mediaseed.storage': storage}), \
                     patch('urllib.request.urlopen', urlopen), patch('signal.signal'), patch('signal.alarm'), \
                     patch('sys.stdin', io.StringIO(json.dumps(config))), contextlib.redirect_stdout(output):
                    exec(compile(watch.WORKER, 'synthetic-worker', 'exec'), {})  # noqa: S102 - fixed worker with synthetic transport
                self.assertNotIn('synthetic-private-key', output.getvalue())
                return json.loads(output.getvalue())
            self.assertTrue(run()['recheckAccepted'])
            self.assertEqual([r['path'] for r in requests if r['method'] == 'POST'], ['torrents/stop', 'torrents/recheck'])
            requests.clear();row['state'] = 'checkingDL'
            self.assertEqual(run()['skipped'], 'state_changed')
            self.assertFalse(any(r['method'] == 'POST' for r in requests))
            requests.clear();row['state'] = 'error';ready[0] = False
            self.assertEqual(run()['skipped'], 'vpn_unavailable')
            self.assertFalse(any(r['method'] == 'POST' for r in requests))
            requests.clear();ready[0] = True;probe.unlink()
            self.assertEqual(run()['errorType'], 'FileNotFoundError')
            self.assertFalse(any(r['method'] == 'POST' for r in requests))
            probe.write_bytes(b'x' * 8192);requests.clear();fail_stop[0] = True
            self.assertEqual(run()['errorType'], 'OSError')
            self.assertNotIn('torrents/recheck', [r['path'] for r in requests])


if __name__ == '__main__':
    unittest.main()
