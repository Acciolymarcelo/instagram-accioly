import copy
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import publisher as p


NOW = dt.datetime(2026, 11, 7, 15, 0, tzinfo=dt.timezone.utc)
CAPTION = 'Legenda literal: saúde, músculo e atenção.\n\nNão cortar a explicação.\n'


def piece(count=20):
    result = {'slug': 'test-piece', 'caption': CAPTION, 'title': 'Peça',
              'scheduled_at': '2026-11-07T12:00:00-03:00',
              'images': [{'number': i, 'sha256': str(i % 10) * 64} for i in range(1, count + 1)]}
    result['content_sha256'] = p.content_fingerprint(result)
    return result


def urls(count=20):
    return [f'https://media.example.net/slide_{i:02}.jpg' for i in range(1, count + 1)]


def state():
    return {'account': p.EXPECTED_ACCOUNT, 'user_id': None, 'pieces': {}}


class FakeGraph:
    user_id = '123456'
    version = 'v27.0'

    def __init__(self, count=20, fail_publish=False, events=None):
        self.calls, self.counter, self.parent, self.did_publish = [], 100, None, False
        self.count, self.fail_publish, self.events = count, fail_publish, events if events is not None else []

    def check_identity(self):
        return {'user_id': self.user_id, 'username': p.EXPECTED_ACCOUNT}

    def check_quota(self):
        return {'usage': 0, 'total': 50}

    def request(self, method, path, params=None):
        params = dict(params or {})
        self.calls.append((method, path, params))
        self.events.append(('request', method, path))
        if method == 'POST' and path.endswith('/media_publish'):
            self.did_publish = True
            if self.fail_publish:
                raise p.GraphError('Simulated transport failure.', uncertain=True)
            return {'id': '999'}
        if method == 'POST':
            self.counter += 1
            if params.get('media_type') == 'CAROUSEL':
                self.parent = str(self.counter)
            return {'id': str(self.counter)}
        if path == '999':
            return {'id': '999', 'caption': CAPTION, 'media_type': 'CAROUSEL_ALBUM',
                    'children': {'data': [{'id': str(i)} for i in range(self.count)]},
                    'permalink': 'https://www.instagram.com/p/test-only-fixture/'}
        return {'status_code': 'PUBLISHED' if path == self.parent and self.did_publish else 'FINISHED'}


class PublisherTests(unittest.TestCase):
    def publisher(self, fake=None, st=None, save=None, now=None):
        return p.Publisher(fake or FakeGraph(), st if st is not None else state(), save or (lambda: None),
                           now=now or (lambda: NOW), sleep=lambda _: None, verify_image=lambda *_: None)

    def test_twenty_images_preserve_order_and_caption(self):
        fake, st = FakeGraph(), state()
        result = self.publisher(fake, st).publish(piece(), urls(), 900)
        creates = [x for x in fake.calls if x[0] == 'POST' and x[1].endswith('/media')]
        self.assertEqual([x[2]['image_url'] for x in creates[:-1]], urls())
        self.assertEqual(creates[-1][2]['children'], ','.join(str(i) for i in range(101, 121)))
        self.assertEqual(creates[-1][2]['caption'].encode(), CAPTION.encode())
        self.assertEqual(result['state'], 'published')
        self.assertTrue(result['readback_verified'])
        self.assertEqual(sum(x[1].endswith('/media_publish') for x in fake.calls), 1)

    def test_twenty_one_rejected_before_network(self):
        fake = FakeGraph()
        with self.assertRaises(p.PublisherError):
            self.publisher(fake).publish(piece(21), urls(21), 900)
        self.assertEqual(fake.calls, [])

    def test_missing_or_duplicate_url_rejected_at_core_boundary(self):
        fake = FakeGraph()
        for value in (urls(2), [urls()[0]] * 20):
            with self.assertRaises(p.PublisherError):
                self.publisher(fake).publish(piece(), value, 900)
        self.assertEqual(fake.calls, [])

    def test_wrong_account_stops_before_post(self):
        client = p.GraphClient('test-token-not-real', '123456', 'v27.0')
        with patch.object(client, 'request', return_value={'user_id': '123456', 'username': 'wrong_account'}) as request:
            with self.assertRaises(p.PublisherError):
                self.publisher(client).publish(piece(), urls(), 900)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], 'GET')

    def test_missing_user_id_is_discovered_read_only_for_approved_account(self):
        with patch.dict('os.environ', {'IG_ACCESS_TOKEN': 'test-token-not-real', 'IG_GRAPH_VERSION': 'v27.0'}, clear=True):
            client = p.client_from_env()
        self.assertIsNone(client.user_id)
        responses = [{'user_id': '123456', 'username': p.EXPECTED_ACCOUNT},
                     {'data': [{'quota_usage': 0, 'config': {'quota_total': 50}}]}]
        with patch.object(client, 'request', side_effect=responses) as request:
            self.assertEqual(client.check_identity(), {'user_id': '123456', 'username': p.EXPECTED_ACCOUNT})
            self.assertEqual(client.user_id, '123456')
            self.assertEqual(client.check_quota(), {'usage': 0, 'total': 50})
        self.assertEqual(request.call_args_list[0].args, ('GET', 'me', {'fields': 'user_id,username'}))
        self.assertEqual(request.call_args_list[1].args[1], '123456/content_publishing_limit')
        self.assertTrue(all(call.args[0] == 'GET' for call in request.call_args_list))

    def test_missing_user_id_wrong_username_aborts_without_adopting_id(self):
        client = p.GraphClient('test-token-not-real', None, 'v27.0')
        with patch.object(client, 'request', return_value={'user_id': '123456', 'username': 'wrong_account'}) as request:
            with self.assertRaises(p.PublisherError):
                self.publisher(client).publish(piece(), urls(), 900)
        self.assertIsNone(client.user_id)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], 'GET')

    def test_discovery_rejects_invalid_id_and_preserves_supplied_id(self):
        for invalid in (None, True, 123.4, '../media', ''):
            client = p.GraphClient('test-token-not-real', '', 'v27.0')
            with patch.object(client, 'request', return_value={'user_id': invalid, 'username': p.EXPECTED_ACCOUNT}):
                with self.subTest(invalid=invalid), self.assertRaises(p.PublisherError):
                    client.check_identity()
            self.assertIsNone(client.user_id)
        client = p.GraphClient('test-token-not-real', '123456', 'v27.0')
        with patch.object(client, 'request', return_value={'user_id': '654321', 'username': p.EXPECTED_ACCOUNT}):
            with self.assertRaises(p.PublisherError):
                client.check_identity()
        self.assertEqual(client.user_id, '123456')

    def test_uncertain_publish_is_reconciled_read_only(self):
        fake, st = FakeGraph(fail_publish=True), state()
        pub = self.publisher(fake, st)
        first = pub.publish(piece(), urls(), 900)
        self.assertEqual(first['state'], 'uncertain')
        posts_before = len([x for x in fake.calls if x[0] == 'POST'])
        second = pub.publish(piece(), urls(), 900)
        self.assertEqual(second['state'], 'published')
        self.assertIsNone(second['media_id'])
        self.assertFalse(second['readback_verified'])
        self.assertEqual(len([x for x in fake.calls if x[0] == 'POST']), posts_before)

    def test_uncertain_not_published_never_replays(self):
        fake, st = FakeGraph(fail_publish=True), state()
        pub = self.publisher(fake, st)
        pub.publish(piece(), urls(), 900)
        fake.did_publish = False
        previous = len(fake.calls)
        self.assertEqual(pub.publish(piece(), urls(), 900)['state'], 'uncertain')
        self.assertTrue(all(c[0] == 'GET' for c in fake.calls[previous:]))

    def test_published_piece_is_idempotent(self):
        fake, st = FakeGraph(), state()
        pub = self.publisher(fake, st)
        pub.publish(piece(), urls(), 900)
        before = len(fake.calls)
        pub.publish(piece(), urls(), 900)
        self.assertEqual(len(fake.calls), before)

    def test_early_and_late_publications_are_rejected(self):
        for delta in (-1, 901):
            fake = FakeGraph()
            with self.assertRaises(p.PublisherError):
                self.publisher(fake, now=lambda: NOW + dt.timedelta(seconds=delta)).publish(piece(), urls(), 900)
            self.assertEqual(fake.calls, [])

    def test_window_rechecked_immediately_before_publish(self):
        fake, st = FakeGraph(count=2), state()
        current = [NOW]
        pub = self.publisher(fake, st, now=lambda: current[0])
        original = pub.poll
        def poll(cid):
            original(cid)
            if cid == fake.parent:
                current[0] = NOW + dt.timedelta(seconds=901)
        pub.poll = poll
        result = pub.publish(piece(2), urls(2), 900)
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(any(x[1].endswith('/media_publish') for x in fake.calls))

    def test_publish_intent_is_synced_before_post(self):
        events, st = [], state()
        fake = FakeGraph(count=2, events=events)
        def save():
            operation = st['pieces'].get('test-piece', {}).get('operations', {}).get('publish')
            if operation:
                events.append(('checkpoint', operation['status']))
        self.publisher(fake, st, save).publish(piece(2), urls(2), 900)
        index = next(i for i, e in enumerate(events) if e == ('request', 'POST', '123456/media_publish'))
        self.assertEqual(events[index - 1], ('checkpoint', 'pending'))

    def test_failed_intent_sync_prevents_publish(self):
        fake, st = FakeGraph(count=2), state()
        def save():
            operation = st['pieces'].get('test-piece', {}).get('operations', {}).get('publish')
            if operation and operation['status'] == 'pending':
                raise p.DurabilityError('Simulated checkpoint failure.')
        with self.assertRaises(p.DurabilityError):
            self.publisher(fake, st, save).publish(piece(2), urls(2), 900)
        self.assertFalse(any(x[1].endswith('/media_publish') for x in fake.calls))

    def test_failed_sync_after_success_does_not_repeat_publish(self):
        fake, st = FakeGraph(count=2), state()
        def save():
            operation = st['pieces'].get('test-piece', {}).get('operations', {}).get('publish')
            if operation and operation['status'] == 'done':
                raise p.DurabilityError('Simulated checkpoint failure.')
        with self.assertRaises(p.DurabilityError):
            self.publisher(fake, st, save).publish(piece(2), urls(2), 900)
        self.assertEqual(st['pieces']['test-piece']['operations']['publish']['result']['id'], '999')
        result = self.publisher(fake, st).publish(piece(2), urls(2), 900)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(sum(x[1].endswith('/media_publish') for x in fake.calls), 1)

    def test_state_permissions_manifest_lock_and_checkpoint(self):
        manifest = {'account': p.EXPECTED_ACCOUNT, 'graph_version': 'v27.0', 'pieces': [piece(2)]}
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime/state.json'
            with p.locked_state(path, manifest, on_save=lambda x: calls.append(x.read_text())) as (st, save):
                st['pieces']['test-piece'] = {'state': 'processing'}
                save()
                with self.assertRaises(p.PublisherError):
                    with p.locked_state(path, manifest):
                        pass
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(calls), 1)
            changed = copy.deepcopy(manifest)
            changed['pieces'][0]['caption'] += 'changed'
            with self.assertRaises(p.PublisherError):
                with p.locked_state(path, changed):
                    pass

    def test_private_and_credential_urls_rejected(self):
        bad = ['http://example.com/a.jpg', 'https://127.0.0.1/a.jpg', 'https://10.0.0.1/a.jpg',
               'https://host.local/a.jpg', 'https://user:pass@example.com/a.jpg',
               'https://example.com/a.jpg?access_token=secret', 'https://example.com/a.jpg?X-Amz-Signature=secret',
               'https://example.com/a.jpg#fragment']
        for url in bad:
            with self.subTest(url=url), self.assertRaises(p.PublisherError):
                p.validate_public_url(url, resolve=False)
        with patch('publisher.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('192.168.1.1', 443))]):
            with self.assertRaises(p.PublisherError):
                p.validate_public_url('https://images.example.net/a.jpg')

    def test_local_preflight_never_claims_live_readiness(self):
        pc = piece(2)
        manifest = {'account': p.EXPECTED_ACCOUNT, 'graph_version': 'v27.0', 'pieces': [pc]}
        media = {'pieces': {pc['slug']: [{'number': a['number'], 'sha256': a['sha256'], 'url': u} for a, u in zip(pc['images'], urls(2))]}}
        with patch('publisher.GraphClient.request', side_effect=AssertionError('No network allowed')):
            report = p.preflight(manifest, media)
        self.assertTrue(report['pieces'][0]['ready'])
        self.assertFalse(report['ready'])
        self.assertFalse(report['live_identity_verified'])

    def test_version_mismatch_and_missing_window_prevent_execution(self):
        manifest = {'account': p.EXPECTED_ACCOUNT, 'graph_version': 'v26.0', 'pieces': [piece(2)]}
        with self.assertRaises(p.PublisherError):
            p.run_due(manifest, {}, '/unused', execute=True, lateness=900, client=FakeGraph())
        manifest['graph_version'] = 'v27.0'
        with self.assertRaises(p.PublisherError):
            p.run_due(manifest, {}, '/unused', execute=True, client=FakeGraph())

    def test_state_sync_subprocess_does_not_receive_ig_token(self):
        with patch.dict('os.environ', {'IG_ACCESS_TOKEN': 'never-leak'}), patch('publisher.subprocess.run') as run:
            p.state_sync_hook('["python3", "sync.py", "{state}"]')(Path('/tmp/state.json'))
            self.assertNotIn('IG_ACCESS_TOKEN', run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.args[0][-1], '/tmp/state.json')
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_reconciled_unverified_publication_returns_nonzero(self):
        manifest = {'account': p.EXPECTED_ACCOUNT, 'graph_version': 'v27.0', 'pieces': [piece(2)]}
        fake = FakeGraph(count=2)
        fake.parent, fake.did_publish = '555', True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p.private_json(root / 'manifest.json', manifest)
            p.private_json(root / 'media.json', {})
            with p.locked_state(root / 'state.json', manifest) as (st, save):
                st['pieces']['test-piece'] = {'state': 'uncertain', 'carousel_id': '555',
                                              'operations': {'publish': {'status': 'pending'}}}
                save()
            argv = ['publisher.py', 'run-due', '--execute', '--max-lateness-seconds', '900',
                    '--manifest', str(root / 'manifest.json'), '--media-urls', str(root / 'media.json'),
                    '--state', str(root / 'state.json')]
            with patch('sys.argv', argv), patch('publisher.client_from_env', return_value=fake), contextlib.redirect_stdout(io.StringIO()):
                code = p.main()
            self.assertEqual(code, 3)
            self.assertTrue(all(x[0] == 'GET' for x in fake.calls))


if __name__ == '__main__':
    unittest.main()
