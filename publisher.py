#!/usr/bin/env python3
"""Instagram Login carousel publisher. Local/dry-run by default; no scheduler installed.

All external mutations require run-due --execute and an explicit lateness window.
Credentials come only from environment variables and are never written or printed.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
EXPECTED_ACCOUNT = 'acciolymarceloo'
DEFAULT_INVENTORY = Path('/workspace/postiz-accioly/tools/work/inventory.json')
DEFAULT_CALENDAR = Path('/workspace/shared/batch5/agendamento-nativo/calendario.csv')
MAX_IMAGE_BYTES = 8 * 1024 * 1024
UTC = dt.timezone.utc


class PublisherError(RuntimeError):
    """Safe to display: messages contain neither credentials nor response bodies."""


class GraphError(PublisherError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class DurabilityError(PublisherError):
    """Stop all mutations when a write-ahead state checkpoint is not confirmed."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def utc_now():
    return dt.datetime.now(UTC)


def stamp(value=None):
    return (value or utc_now()).astimezone(UTC).isoformat()


def parse_time(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        raise PublisherError('Invalid schedule timestamp.') from None
    if parsed.tzinfo is None:
        raise PublisherError('Schedule timestamps must contain an explicit timezone.')
    return parsed.astimezone(UTC)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise PublisherError(f'Cannot read valid JSON: {Path(path).name}.') from None


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def content_fingerprint(piece):
    return sha256(canonical({
        'slug': piece['slug'], 'caption': piece['caption'],
        'images': [{'number': x['number'], 'sha256': x['sha256']} for x in piece['images']],
    }))


def validate_piece(piece):
    if not isinstance(piece, dict) or not re.fullmatch(r'[a-z0-9-]+', piece.get('slug', '')):
        raise PublisherError('Invalid piece identifier.')
    images = piece.get('images')
    if not isinstance(images, list) or not 2 <= len(images) <= 20:
        raise PublisherError('A carousel must contain between 2 and 20 images.')
    if [x.get('number') if isinstance(x, dict) else None for x in images] != list(range(1, len(images) + 1)):
        raise PublisherError('Carousel images must retain contiguous numeric order.')
    caption = piece.get('caption')
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 2200:
        raise PublisherError('Caption must be nonempty and contain at most 2200 characters.')
    for image in images:
        if not re.fullmatch(r'[a-f0-9]{64}', image.get('sha256', '')):
            raise PublisherError('Each image requires a SHA256 checksum.')
    if piece.get('content_sha256') != content_fingerprint(piece):
        raise PublisherError('Piece fingerprint does not match its caption/images/order.')
    if parse_time(piece['scheduled_at']).second:
        raise PublisherError('This batch uses whole-minute scheduled times.')


def prepare(inventory_path, calendar_path, manifest_path, media_path):
    inventory = read_json(inventory_path)
    by_slug = {p['slug']: p for p in inventory['pieces']}
    with Path(calendar_path).open(encoding='utf-8-sig', newline='') as source:
        calendar = list(csv.DictReader(source))
    pieces = []
    media = {'schema_version': 1, 'status': 'missing_public_image_urls', 'pieces': {}}
    seen = set()
    for row in calendar:
        slug = Path(row['Arquivo']).stem
        if slug in seen or slug not in by_slug:
            raise PublisherError('Duplicate or unknown piece in the calendar.')
        seen.add(slug)
        source = by_slug[slug]
        caption = source['caption']
        if sha256(caption.encode()) != source['caption_sha256']:
            raise PublisherError('Original caption checksum mismatch.')
        scheduled = dt.datetime.fromisoformat(row['Data'] + 'T' + row['Hora']).replace(tzinfo=ZoneInfo(row['Fuso']))
        piece = {'slug': slug, 'title': source['title'], 'caption': caption,
                 'scheduled_at': scheduled.isoformat(), 'scheduled_at_utc': stamp(scheduled),
                 'timezone': row['Fuso'], 'images': []}
        media['pieces'][slug] = []
        for asset in source['assets']:
            path = Path(asset['jpeg'])
            content = path.read_bytes()
            if sha256(content) != asset['jpeg_sha256']:
                raise PublisherError('JPEG checksum mismatch; source artwork was not changed.')
            if sha256(Path(asset['source']).read_bytes()) != asset['source_sha256']:
                raise PublisherError('Original PNG checksum mismatch.')
            if not content.startswith(b'\xff\xd8') or len(content) > MAX_IMAGE_BYTES:
                raise PublisherError('The approved export must be JPEG and at most 8 MiB.')
            piece['images'].append({'number': asset['number'], 'sha256': asset['jpeg_sha256'],
                                    'local_file': str(path), 'bytes': len(content)})
            media['pieces'][slug].append({'number': asset['number'], 'sha256': asset['jpeg_sha256'], 'url': None})
        if len(piece['images']) != int(row['Slides']):
            raise PublisherError('Calendar and source image counts disagree.')
        piece['content_sha256'] = content_fingerprint(piece)
        validate_piece(piece)
        pieces.append(piece)
    manifest = {'schema_version': 1, 'account': EXPECTED_ACCOUNT, 'graph_version': 'v27.0',
                'status': 'prepared_not_uploaded_not_scheduled', 'pieces': pieces}
    if Path(manifest_path).exists() and read_json(manifest_path) != manifest:
        raise PublisherError('Existing manifest differs; review it before replacing approved content.')
    private_json(manifest_path, manifest)
    # Preserve real URLs entered by an operator; prepare never overwrites them.
    if not Path(media_path).exists():
        private_json(media_path, media)
    return {'pieces': len(pieces), 'images': sum(len(x['images']) for x in pieces),
            'manifest': str(manifest_path), 'media_urls': str(media_path), 'network_calls': 0}


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get('account') != EXPECTED_ACCOUNT:
        raise PublisherError('Manifest must target acciolymarceloo.')
    pieces = manifest.get('pieces')
    if not isinstance(pieces, list) or not pieces:
        raise PublisherError('Manifest has no pieces.')
    if len({x.get('slug') for x in pieces if isinstance(x, dict)}) != len(pieces):
        raise PublisherError('Manifest has duplicate or invalid identifiers.')
    for piece in pieces:
        validate_piece(piece)


def validate_public_url(value, *, resolve=True):
    if not isinstance(value, str) or not value or value != value.strip():
        raise PublisherError('A real public HTTPS image URL is required.')
    try:
        url = urllib.parse.urlsplit(value)
        port = url.port
    except ValueError:
        raise PublisherError('Invalid media URL.') from None
    host = (url.hostname or '').lower().rstrip('.')
    if url.scheme != 'https' or not host or url.username or url.password or port not in (None, 443) or url.fragment:
        raise PublisherError('Media URLs must use public HTTPS, without embedded credentials or fragments.')
    if '\\' in value or any(ord(c) < 33 for c in value):
        raise PublisherError('Media URL contains prohibited characters.')
    if host == 'localhost' or '.' not in host or host.endswith(('.local', '.internal', '.localhost', '.test', '.invalid', '.example')):
        raise PublisherError('Private/local/example media hosts are prohibited.')
    credential_keys = ('token', 'secret', 'signature', 'credential', 'api_key', 'apikey', 'authorization', 'password')
    for key, _ in urllib.parse.parse_qsl(url.query, keep_blank_values=True):
        if any(marker in key.lower() for marker in credential_keys) or key.lower() in ('sig', 'auth', 'key'):
            raise PublisherError('Credential-bearing or signed media URLs are prohibited.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise PublisherError('Media URLs cannot target private or reserved IP addresses.')
    if resolve and address is None:
        try:
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except (OSError, ValueError):
            raise PublisherError('Cannot establish that the media host resolves publicly.') from None
        if not addresses or any(not ip.is_global for ip in addresses):
            raise PublisherError('Media host resolves to a private or reserved IP address.')
    return value


def piece_urls(piece, media, *, resolve=False):
    rows = media.get('pieces', {}).get(piece['slug'])
    if not isinstance(rows, list) or len(rows) != len(piece['images']):
        raise PublisherError('Public media mapping is missing or incomplete.')
    result = []
    for expected, row in zip(piece['images'], rows):
        if not isinstance(row, dict) or row.get('number') != expected['number'] or row.get('sha256') != expected['sha256']:
            raise PublisherError('Public image mapping does not preserve the approved order and hashes.')
        result.append(validate_public_url(row.get('url'), resolve=resolve))
    if len(set(result)) != len(result):
        raise PublisherError('Different carousel slides cannot reuse a public URL.')
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_remote_image(url, expected_sha):
    validate_public_url(url, resolve=True)
    request = urllib.request.Request(url, headers={'Accept': 'image/jpeg', 'User-Agent': 'AcciolyPublisher/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200 or response.headers.get_content_type() != 'image/jpeg':
                raise PublisherError('A media URL did not return a direct JPEG response.')
            data = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, OSError):
        raise PublisherError('Cannot retrieve a public JPEG; no credential or URL was logged.') from None
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(b'\xff\xd8') or sha256(data) != expected_sha:
        raise PublisherError('Public JPEG differs from the approved image or exceeds the size limit.')


class GraphClient:
    def __init__(self, token, user_id, version, *, login_mode='instagram'):
        if not token or any(c.isspace() for c in token):
            raise PublisherError('IG_ACCESS_TOKEN is missing or malformed.')
        if user_id not in (None, '') and (not isinstance(user_id, str) or not re.fullmatch(r'[0-9]{1,64}', user_id)):
            raise PublisherError('Optional IG_USER_ID must be the Instagram professional account ID.')
        if not re.fullmatch(r'v[0-9]{1,2}\.0', version or ''):
            raise PublisherError('IG_GRAPH_VERSION must be explicit, for example v27.0.')
        if login_mode not in ('instagram', 'unified'):
            raise PublisherError('IG_LOGIN_MODE must be instagram or unified.')
        self.token, self.user_id, self.version = token, user_id or None, version
        self.login_mode = login_mode
        host = 'graph.facebook.com' if login_mode == 'unified' else 'graph.instagram.com'
        self.base = f'https://{host}/{version}'
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, params=None):
        assigned_accounts = (self.login_mode == 'unified' and method == 'GET' and
                             path == 'me/assigned_instagram_accounts')
        if not assigned_accounts and not re.fullmatch(r'(me|[0-9]{1,64})(/(media|media_publish|content_publishing_limit))?', path):
            raise PublisherError('Unsupported Graph endpoint.')
        encoded = urllib.parse.urlencode(params or {}).encode()
        url = self.base + '/' + path
        if method == 'GET' and encoded:
            url += '?' + encoded.decode()
        request = urllib.request.Request(url, method=method, data=encoded if method == 'POST' else None,
                    headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/x-www-form-urlencoded',
                             'Accept': 'application/json', 'User-Agent': 'AcciolyPublisher/1.0'})
        try:
            with self.opener.open(request, timeout=45) as response:
                raw = response.read(2 * 1024 * 1024)
            value = json.loads(raw)
            if not isinstance(value, dict) or 'error' in value:
                raise GraphError('Graph returned an unexpected response.', uncertain=method == 'POST')
            return value
        except urllib.error.HTTPError as exc:
            code = exc.code
            raise GraphError(f'Graph HTTP {code}; response body omitted.', uncertain=method == 'POST' and code >= 500) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise GraphError('Graph transport or response error; details omitted.', uncertain=method == 'POST') from None

    def _assigned_account(self):
        params = {'fields': 'id,username'}
        matches, seen_cursors = [], set()
        while True:
            page = self.request('GET', 'me/assigned_instagram_accounts', params)
            accounts = page.get('data')
            if not isinstance(accounts, list) or not all(
                    isinstance(account, dict) and isinstance(account.get('username'), str)
                    for account in accounts):
                raise PublisherError('Assigned Instagram account response is unavailable or malformed.')
            matches.extend(account for account in accounts if account['username'].casefold() == EXPECTED_ACCOUNT)
            if len(matches) > 1:
                break
            paging = page.get('paging', {})
            if not isinstance(paging, dict):
                raise PublisherError('Assigned Instagram account pagination is malformed.')
            if paging.get('next') is None:
                break
            # Rebuild the request on our Graph host. The returned URL can contain a token.
            cursors = paging.get('cursors')
            cursor = cursors.get('after') if isinstance(cursors, dict) else None
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise PublisherError('Assigned Instagram account pagination is malformed.')
            seen_cursors.add(cursor)
            params['after'] = cursor
        if len(matches) != 1:
            raise PublisherError('The approved Instagram account must be assigned exactly once.')
        return matches[0]

    def check_identity(self):
        if self.login_mode == 'unified':
            result = self._assigned_account()
            remote_value = result.get('id')
        else:
            result = self.request('GET', 'me', {'fields': 'user_id,username'})
            remote_value = result.get('user_id')
        username = result.get('username')
        remote_id = str(remote_value) if type(remote_value) in (str, int) else ''
        if (not isinstance(username, str) or username.casefold() != EXPECTED_ACCOUNT or
                not re.fullmatch(r'[0-9]{1,64}', remote_id) or
                (self.user_id is not None and remote_id != self.user_id)):
            raise PublisherError('Authenticated Instagram identity does not match the approved account.')
        # Discover only after validating the fixed approved username and ID shape.
        # The token remains in the Authorization header handled by request().
        self.user_id = remote_id
        return {'user_id': self.user_id, 'username': EXPECTED_ACCOUNT}

    def check_quota(self):
        if self.user_id is None:
            self.check_identity()
        result = self.request('GET', self.user_id + '/content_publishing_limit', {'fields': 'quota_usage,config'})
        data = result.get('data')
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise PublisherError('Publishing quota response is unavailable or malformed.')
        usage, config = data[0].get('quota_usage'), data[0].get('config')
        total = config.get('quota_total') if isinstance(config, dict) else None
        if type(usage) is not int or type(total) is not int or not 0 <= usage < total:
            raise PublisherError('Publishing quota is unavailable or exhausted.')
        return {'usage': usage, 'total': total}


def client_from_env():
    return GraphClient(os.environ.get('IG_ACCESS_TOKEN', ''), os.environ.get('IG_USER_ID', ''),
                       os.environ.get('IG_GRAPH_VERSION', ''), login_mode=os.environ.get('IG_LOGIN_MODE', 'instagram'))


@contextlib.contextmanager
def locked_state(path, manifest, on_save=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_suffix(path.suffix + '.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublisherError('Another publisher owns this state lock.') from None
        fingerprint = sha256(canonical(manifest))
        if path.exists():
            if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
                raise PublisherError('State must be an owned regular private file (0600).')
            state = read_json(path)
            if state.get('manifest_sha256') != fingerprint:
                raise PublisherError('Manifest changed after state was created; explicit reconciliation required.')
        else:
            state = {'schema_version': 1, 'manifest_sha256': fingerprint, 'account': EXPECTED_ACCOUNT,
                     'user_id': None, 'pieces': {}}
        def save():
            private_json(path, state)
            if on_save:
                try:
                    on_save(path)
                except Exception:
                    raise DurabilityError('State checkpoint synchronization was not confirmed; no further writes are permitted.') from None
        yield state, save
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def safe_id(result):
    value = str(result.get('id', '')) if isinstance(result, dict) else ''
    if not re.fullmatch(r'[0-9]{1,64}', value):
        raise GraphError('Mutation response has no valid ID; reconcile before retrying.', uncertain=True)
    return value


class Publisher:
    def __init__(self, client, state, save, *, now=utc_now, sleep=time.sleep, verify_image=verify_remote_image,
                 poll_timeout=300, poll_interval=60):
        self.client, self.state, self.save = client, state, save
        self.now, self.sleep, self.verify_image = now, sleep, verify_image
        self.poll_timeout, self.poll_interval = poll_timeout, poll_interval

    def ensure_window(self, piece, lateness):
        delta = (self.now() - parse_time(piece['scheduled_at'])).total_seconds()
        if delta < 0 or delta > lateness:
            raise PublisherError('Publication is outside its explicit authorized time window.')

    def poll(self, container_id):
        started = time.monotonic()
        while True:
            result = self.client.request('GET', container_id, {'fields': 'status_code,status'})
            status = result.get('status_code')
            if status == 'FINISHED':
                return
            if status in ('ERROR', 'EXPIRED', 'PUBLISHED'):
                raise PublisherError('Container has an unexpected terminal status: ' + status)
            if status != 'IN_PROGRESS' or time.monotonic() - started >= self.poll_timeout:
                raise PublisherError('Container readiness was not confirmed within the bounded polling window.')
            self.sleep(self.poll_interval)

    def post_once(self, entry, key, path, params):
        operations = entry.setdefault('operations', {})
        previous = operations.get(key)
        if previous:
            if previous.get('status') == 'done':
                return previous['result']
            raise GraphError('An earlier mutation is pending or failed; reconcile instead of replaying.', uncertain=True)
        operations[key] = {'status': 'pending', 'started_at': stamp(self.now())}
        self.save()  # Durable write-ahead record: never replay a POST after an interrupted result.
        try:
            result = self.client.request('POST', path, params)
            safe_id(result)
        except GraphError as exc:
            operations[key]['status'] = 'uncertain' if exc.uncertain else 'failed'
            operations[key]['error'] = str(exc)
            self.save()
            raise
        operations[key] = {'status': 'done', 'completed_at': stamp(self.now()),
                           'result': {k: result[k] for k in ('id', 'config_issue') if k in result}}
        self.save()
        return operations[key]['result']

    def reconcile_uncertain(self, entry):
        """Read-only reconciliation. Never sends another media_publish request."""
        parent = entry.get('carousel_id')
        if not parent or not entry.get('operations', {}).get('publish'):
            return
        try:
            result = self.client.request('GET', parent, {'fields': 'status_code'})
            entry['last_container_status'] = result.get('status_code')
            if result.get('status_code') == 'PUBLISHED':
                entry['state'] = 'published'
                entry['publication_confirmation'] = 'container_status_PUBLISHED'
                entry['media_id'] = entry.get('media_id')  # Unknown remains null; no invented permalink.
                entry['readback_verified'] = False
                entry['verification_issue'] = 'Container is published; published-media ID/caption readback is unresolved.'
        except PublisherError:
            entry['reconciliation_read_failed'] = True
        self.save()

    def verify_published(self, piece, entry):
        try:
            actual = self.client.request('GET', entry['media_id'], {'fields': 'id,caption,media_type,permalink,children'})
            children = actual.get('children')
            children_data = children.get('data') if isinstance(children, dict) else None
            caption = piece['caption']
            # Meta can omit a single final newline when returning the published caption.
            caption_matches = actual.get('caption') in (caption, caption.removesuffix('\n'))
            entry['readback_verified'] = (
                str(actual.get('id')) == entry['media_id'] and caption_matches and
                actual.get('media_type') == 'CAROUSEL_ALBUM' and isinstance(children_data, list) and
                len(children_data) == len(piece['images']) and not entry.get('config_issue'))
            link = actual.get('permalink')
            if isinstance(link, str) and link.startswith('https://www.instagram.com/'):
                entry['permalink'] = link
            if entry['readback_verified']:
                entry.pop('verification_issue', None)
            else:
                entry['verification_issue'] = 'Published, but caption/type/image-count readback did not fully match.'
        except PublisherError:
            entry['verification_issue'] = 'Published ID confirmed; readback unavailable.'
        self.save()

    def publish(self, piece, urls, lateness):
        validate_piece(piece)
        if self.state.get('account') != EXPECTED_ACCOUNT:
            raise PublisherError('State does not target the approved account.')
        if not isinstance(urls, list) or len(urls) != len(piece['images']) or len(set(urls)) != len(urls):
            raise PublisherError('Publisher requires exactly one distinct URL for every approved slide.')
        for url in urls:
            validate_public_url(url, resolve=False)
        slug = piece['slug']
        entry = self.state['pieces'].setdefault(slug, {'state': 'prepared', 'content_sha256': piece['content_sha256'], 'operations': {}})
        if entry.get('content_sha256') != piece['content_sha256']:
            raise PublisherError('Previously tracked piece content has changed; do not replay it.')
        if entry['state'] == 'published':
            return entry
        if entry['state'] in ('uncertain', 'processing'):
            entry['state'] = 'uncertain'
            self.reconcile_uncertain(entry)
            return entry
        if entry['state'] == 'failed':
            return entry
        self.ensure_window(piece, lateness)
        self.client.check_identity()
        if self.state.get('user_id') not in (None, self.client.user_id):
            raise PublisherError('State belongs to another Instagram user ID.')
        self.state['user_id'] = self.client.user_id
        self.client.check_quota()
        # Verify every original JPEG at its remote URL before creating any container.
        for url, image in zip(urls, piece['images']):
            self.verify_image(url, image['sha256'])
        self.ensure_window(piece, lateness)
        entry['state'], entry['started_at'] = 'processing', stamp(self.now())
        self.save()
        try:
            children = []
            for index, url in enumerate(urls, 1):
                self.ensure_window(piece, lateness)
                result = self.post_once(entry, f'child_{index:02}', self.client.user_id + '/media',
                                        {'image_url': url, 'is_carousel_item': 'true'})
                child_id = safe_id(result)
                children.append(child_id)
                entry['child_ids'] = children[:]
                self.save()
                self.poll(child_id)
            self.ensure_window(piece, lateness)
            result = self.post_once(entry, 'carousel', self.client.user_id + '/media',
                                    {'media_type': 'CAROUSEL', 'children': ','.join(children), 'caption': piece['caption']})
            entry['carousel_id'] = safe_id(result)
            self.save()
            self.poll(entry['carousel_id'])
            self.ensure_window(piece, lateness)
            result = self.post_once(entry, 'publish', self.client.user_id + '/media_publish', {'creation_id': entry['carousel_id']})
            entry.update(state='published', media_id=safe_id(result), published_at=stamp(self.now()),
                         publication_confirmation='media_publish_returned_id', readback_verified=False)
            if result.get('config_issue'):
                entry['config_issue'] = result['config_issue']
            self.save()  # A failed GET after this point must never replay publication.
            self.verify_published(piece, entry)
        except DurabilityError:
            raise
        except GraphError as exc:
            entry['state'] = 'uncertain' if exc.uncertain else 'failed'
            entry['error'] = str(exc)
            self.save()
        except PublisherError as exc:
            entry['state'], entry['error'] = 'failed', str(exc)
            self.save()
        return entry


def preflight(manifest, media, execute=False, client=None):
    validate_manifest(manifest)
    report = {'mode': 'live_read_only' if execute else 'local_only', 'ready': True,
              'account': EXPECTED_ACCOUNT, 'pieces': [], 'remote_writes': 0}
    if execute:
        client = client or client_from_env()
        if client.version != manifest.get('graph_version'):
            raise PublisherError('Configured Graph version differs from the reviewed manifest.')
        report['identity'] = client.check_identity()
        report['quota'] = client.check_quota()
    for piece in manifest['pieces']:
        item = {'slug': piece['slug'], 'images': len(piece['images']), 'ready': False}
        try:
            urls = piece_urls(piece, media, resolve=execute)
            if execute:
                for url, image in zip(urls, piece['images']):
                    verify_remote_image(url, image['sha256'])
            item['ready'] = True
        except PublisherError as exc:
            item['reason'] = str(exc)
            report['ready'] = False
        report['pieces'].append(item)
    if not execute:
        report['credentials_configured'] = all(os.environ.get(k) for k in ('IG_ACCESS_TOKEN', 'IG_GRAPH_VERSION'))
        report['optional_user_id_configured'] = bool(os.environ.get('IG_USER_ID'))
        report['live_identity_verified'] = False
        report['ready'] = False  # A local file check never claims publishing readiness.
    return report


def run_due(manifest, media, state_path, *, execute=False, lateness=None, now=None, client=None, on_save=None):
    validate_manifest(manifest)
    now = now or utc_now()
    if lateness is not None and (type(lateness) is not int or not 0 <= lateness <= 3600):
        raise PublisherError('Lateness must be an explicit 0–3600 second window.')
    if execute and lateness is None:
        raise PublisherError('--execute requires --max-lateness-seconds; no late publication is implicit.')
    report = {'mode': 'execute' if execute else 'dry_run', 'checked_at': stamp(now), 'pieces': []}
    if not execute:
        for piece in manifest['pieces']:
            delta = (now - parse_time(piece['scheduled_at'])).total_seconds()
            status = 'future' if delta < 0 else ('window_required' if lateness is None else ('due' if delta <= lateness else 'expired_window'))
            try:
                piece_urls(piece, media)
                urls_ready = True
            except PublisherError:
                urls_ready = False
            report['pieces'].append({'slug': piece['slug'], 'timing': status, 'public_urls_ready': urls_ready})
        report['remote_calls'] = 0
        return report
    client = client or client_from_env()
    if client.version != manifest.get('graph_version'):
        raise PublisherError('Configured Graph version differs from the reviewed manifest.')
    with locked_state(state_path, manifest, on_save) as (state, save):
        publisher = Publisher(client, state, save)
        for piece in manifest['pieces']:
            entry = state['pieces'].get(piece['slug'])
            if entry and entry.get('state') in ('published', 'failed'):
                if entry['state'] == 'published' and entry.get('media_id') and entry.get('readback_verified') is False:
                    client.check_identity()
                    if state.get('user_id') not in (None, client.user_id):
                        raise PublisherError('State belongs to another Instagram user ID.')
                    publisher.verify_published(piece, entry)
                report['pieces'].append({'slug': piece['slug'], 'state': entry['state'], 'reused': True,
                                         'readback_verified': entry.get('readback_verified'),
                                         'verification_issue': entry.get('verification_issue')})
                continue
            if entry and entry.get('state') in ('processing', 'uncertain'):
                client.check_identity()
                entry['state'] = 'uncertain'
                publisher.reconcile_uncertain(entry)
                report['pieces'].append({'slug': piece['slug'], 'state': entry['state'], 'reconciled_read_only': True,
                                         'readback_verified': entry.get('readback_verified'),
                                         'verification_issue': entry.get('verification_issue')})
                continue
            delta = (utc_now() - parse_time(piece['scheduled_at'])).total_seconds()
            if delta < 0 or delta > lateness:
                report['pieces'].append({'slug': piece['slug'], 'state': 'future' if delta < 0 else 'expired_window'})
                continue
            urls = piece_urls(piece, media, resolve=True)
            entry = publisher.publish(piece, urls, lateness)
            report['pieces'].append({key: entry.get(key) for key in ('state', 'media_id', 'permalink', 'readback_verified', 'error')})
            report['pieces'][-1]['slug'] = piece['slug']
    return report


def state_sync_hook(command_json):
    if not command_json:
        return None
    try:
        argv = json.loads(command_json)
    except ValueError:
        raise PublisherError('State synchronization command must be a JSON argv array.') from None
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x and '\x00' not in x for x in argv):
        raise PublisherError('State synchronization command must be a nonempty JSON argv array.')
    if argv.count('{state}') != 1 or any('{state}' in x and x != '{state}' for x in argv):
        raise PublisherError('State synchronization command requires one literal {state} argument.')
    def sync(path):
        environment = os.environ.copy()
        environment.pop('IG_ACCESS_TOKEN', None)
        subprocess.run([str(path.resolve()) if x == '{state}' else x for x in argv],
                       env=environment, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    return sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('prepare', 'preflight', 'run-due'), default='prepare')
    parser.add_argument('--inventory', type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument('--calendar', type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'manifest.json')
    parser.add_argument('--media-urls', type=Path, default=ROOT / 'media-urls.json')
    parser.add_argument('--state', type=Path, default=ROOT / 'runtime/state.json')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--max-lateness-seconds', type=int)
    parser.add_argument('--state-sync-command', help='Trusted local command as JSON argv; one argument must be {state}.')
    parser.add_argument('--require-state-sync', action='store_true', help='Fail closed if --execute has no durable checkpoint hook.')
    args = parser.parse_args()
    if args.command == 'prepare':
        if args.execute:
            raise PublisherError('prepare is local only; omit --execute.')
        result = prepare(args.inventory, args.calendar, args.manifest, args.media_urls)
    else:
        manifest, media = read_json(args.manifest), read_json(args.media_urls)
        hook = state_sync_hook(args.state_sync_command)
        if args.command == 'run-due' and args.execute and args.require_state_sync and hook is None:
            raise PublisherError('This executor requires --state-sync-command for durable publication checkpoints.')
        result = (preflight(manifest, media, args.execute) if args.command == 'preflight' else
                  run_due(manifest, media, args.state, execute=args.execute, lateness=args.max_lateness_seconds, on_save=hook))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.execute:
        if args.command == 'preflight' and not result.get('ready'):
            return 3
        if args.command == 'run-due' and any(
            p.get('state') in ('failed', 'uncertain', 'expired_window') or
            (p.get('state') == 'published' and p.get('readback_verified') is False)
            for p in result.get('pieces', [])
        ):
            return 3
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except PublisherError as exc:
        print(json.dumps({'error': str(exc), 'published_not_assumed': True}, ensure_ascii=False))
        raise SystemExit(2)
    except (OSError, KeyError, TypeError, ValueError):
        print(json.dumps({'error': 'Invalid local state, configuration or filesystem operation; details omitted.'}))
        raise SystemExit(2)
