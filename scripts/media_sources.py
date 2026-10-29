# -*- coding: utf-8 -*-
"""Authorized media discovery and streaming downloads, without persistent credentials.

This module never logs complete signed URLs, cookies, or authorization headers.
No DRM decryption or authentication bypass is provided.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

import requests

MEDIA_EXTENSIONS = {'.mp4': 'mp4', '.m4v': 'mp4', '.mov': 'mp4', '.webm': 'webm', '.mkv': 'mkv'}
STREAM_EXTENSIONS = {'.m3u8', '.mpd'}
ACCEPTED_HEADERS = ('user-agent', 'referer', 'authorization', 'cookie', 'accept')
RESTRICTED_SUFFIXES = ('.local', '.internal', '.localhost')


class MediaDownloadError(Exception):
    """Non-sensitive error; should not contain signed URLs or credentials."""


class AuthenticationExpired(MediaDownloadError):
    """Source returned 401 or 403; replay the authorized video after signing in."""


def source_id(url: str) -> str:
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def source_host(url: str) -> str:
    return (urlsplit(url).hostname or '').lower()


def media_kind(url: str, content_type: str = '') -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        return None
    extension = Path(parts.path.lower()).suffix
    mime = content_type.lower().split(';')[0].strip()
    if extension in STREAM_EXTENSIONS or mime in (
        'application/vnd.apple.mpegurl', 'application/x-mpegurl',
        'application/dash+xml', 'audio/mpegurl'
    ):
        return 'playlist'
    if extension in MEDIA_EXTENSIONS:
        return MEDIA_EXTENSIONS[extension]
    if mime in ('video/mp4', 'video/x-m4v', 'video/quicktime'):
        return 'mp4'
    if mime == 'video/webm':
        return 'webm'
    if mime in ('video/x-matroska', 'video/x-mkv'):
        return 'mkv'
    return None


def host_allowed(host: str, approved: list[str]) -> bool:
    """Exact host or '*.example.org'; never substring match a hostname."""
    host = host.lower().rstrip('.')
    for entry in approved:
        rule = entry.lower().strip().rstrip('.')
        if rule.startswith('*.'):
            base = rule[2:]
            if base and host.endswith('.' + base) and host != base:
                return True
        elif host == rule:
            return True
    return False


def is_private_host(host: str) -> bool:
    """Reject obvious private/local hosts; no DNS lookups or unsolicited probing."""
    if not host or host == 'localhost' or host.endswith(RESTRICTED_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host.strip('[]'))
        return not ip.is_global
    except ValueError:
        return False


def safe_filename(title: str, identifier: str, extension: str) -> str:
    # Never use URL query strings or authorization tokens in filenames.
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', title)
    title = re.sub(r'\s+', ' ', title).strip(' .')[:70].strip(' .') or 'video'
    return f'{title}-{identifier[:10]}.{extension}'


@dataclass
class MediaCandidate:
    url: str
    kind: str
    content_type: str = ''
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    title: str = ''

    @property
    def key(self):
        return source_id(self.url)

    @property
    def host(self):
        return source_host(self.url)

    def public_info(self):
        # No URL, referer, cookie, query, page token, authorization or raw headers.
        return {'id': self.key, 'host': self.host, 'type': self.kind}


def make_candidate(url: str, content_type: str = '', headers=None, title='') -> MediaCandidate | None:
    kind = media_kind(url, content_type)
    if kind is None:
        return None
    head = {}
    for key, value in (headers or {}).items():
        if key.lower() in ACCEPTED_HEADERS and isinstance(value, str):
            head[key.lower()] = value
    return MediaCandidate(url=url, kind=kind, content_type=content_type,
                          headers=head, title=title or 'video')


class CaptureBuffer:
    """In-memory deduplication of network-observed video resources."""

    def __init__(self):
        self.candidates: dict[str, MediaCandidate] = {}

    def add(self, url: str, content_type: str = '', headers=None, title='') -> bool:
        item = make_candidate(url, content_type, headers=headers, title=title)
        if item is None:
            return False
        self.candidates.setdefault(item.key, item)
        return True

    def list(self):
        return list(self.candidates.values())

    def hosts(self):
        return dict(sorted(Counter(c.host for c in self.candidates.values()).items()))


def collect_from_har(path: Path) -> CaptureBuffer:
    """Read a *local, sensitive* HAR export; do not retain/duplicate the raw HAR."""
    path = Path(path).expanduser()
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError('HAR exceeds 100 MB. Export a smaller network capture.')
    with path.open(encoding='utf-8-sig') as f:
        data = json.load(f)
    items = data.get('log', {}).get('entries', [])
    if not isinstance(items, list):
        raise ValueError('Invalid HAR: log.entries is not a list')
    result = CaptureBuffer()
    for entry in items:
        req = entry.get('request', {})
        res = entry.get('response', {})
        url = req.get('url', '')
        if (req.get('method', 'GET') or 'GET').upper() != 'GET':
            continue
        if not isinstance(url, str):
            continue
        mime = res.get('content', {}).get('mimeType', '') or ''
        h = {x.get('name', '').lower(): x.get('value', '')
             for x in req.get('headers', []) if isinstance(x, dict)}
        # HAR cookie arrays may contain credentials even when Cookie header is absent.
        if 'cookie' not in h:
            cookies = req.get('cookies', [])
            if isinstance(cookies, list):
                values = [(x.get('name'), x.get('value')) for x in cookies
                          if isinstance(x, dict) and x.get('name') and x.get('value')]
                if values:
                    h['cookie'] = '; '.join(f'{name}={value}' for name, value in values)
        # No raw referrer URLs are written to disk. They remain transient in memory.
        result.add(url, mime, h)
    return result


def _sanitize_candidate_headers(candidate: MediaCandidate, *, first_host: str) -> dict[str, str]:
    headers = {'User-Agent': candidate.headers.get('user-agent', 'meeting-slide-pdf-pipeline/1.3'),
               'Accept': 'video/*,application/octet-stream;q=0.8,*/*;q=0.3'}
    if candidate.host == first_host:
        for key in ('referer', 'authorization', 'cookie'):
            if candidate.headers.get(key):
                headers[key.title() if key != 'referer' else 'Referer'] = candidate.headers[key]
    return headers


def _validated_link(url: str, approved_hosts: list[str], allow_private: bool) -> None:
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise MediaDownloadError('Invalid HTTP(S) video URL or credentials embedded in the URL')
    if not host_allowed(p.hostname, approved_hosts):
        raise MediaDownloadError(f'Media host {p.hostname} is not explicitly approved')
    if is_private_host(p.hostname) and not allow_private:
        raise MediaDownloadError('Private or local network hosts require --allow-private-hosts')


def download_media(candidate: MediaCandidate, output: Path, approved_hosts: list[str],
                   *, allow_private=False, max_bytes=8 * 1024**3, session=None,
                   max_redirects=4, timeout=(20, 90)) -> dict:
    """Download an authorized video; preserve incomplete .part for Range resume.

    Redirects are manually reviewed and must also match the allowlist. On
    cross-host redirects, never forward captured Cookie/Authorization headers.
    """
    if candidate.kind == 'playlist':
        raise MediaDownloadError('HLS/DASH manifests detected; this adapter handles direct video files only')
    if output.exists() and output.stat().st_size:
        return {'status': 'skipped', 'file': output.name, 'bytes': output.stat().st_size}
    if output.exists():
        raise MediaDownloadError('Empty destination already exists; rename or remove it manually')
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(output.name + '.part')
    offset = part.stat().st_size if part.exists() else 0
    if offset > max_bytes:
        raise MediaDownloadError('Existing partial download exceeds configured size limit')
    url = candidate.url
    origin_host = candidate.host
    owned_session = session is None
    session = session or requests.Session()
    try:
        for _ in range(max_redirects + 1):
            _validated_link(url, approved_hosts, allow_private)
            headers = _sanitize_candidate_headers(candidate, first_host=origin_host)
            if urlsplit(url).netloc.lower() != urlsplit(candidate.url).netloc.lower():
                headers.pop('Cookie', None)
                headers.pop('Authorization', None)
                headers.pop('Referer', None)
            if offset:
                headers['Range'] = f'bytes={offset}-'
            try:
                response = session.get(url, headers=headers, allow_redirects=False,
                                       stream=True, timeout=timeout)
            except requests.RequestException as exc:
                raise MediaDownloadError('Network request failed; partial download retained') from None
            with response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location', '')
                    if not location:
                        raise MediaDownloadError('Redirect response omitted Location')
                    next_url = urljoin(url, location)
                    if urlsplit(next_url).netloc.lower() != urlsplit(url).netloc.lower():
                        # The HTTP cookie jar is hostname-scoped, not port-scoped.
                        # A cross-port redirect must not inherit stored cookies.
                        session.cookies.clear()
                    url = next_url
                    continue
                if response.status_code in (401, 403):
                    raise AuthenticationExpired('Media returned HTTP 401/403; sign in again, replay, and re-capture')
                if response.status_code >= 400:
                    raise MediaDownloadError(f'Media server returned HTTP {response.status_code}')
                if response.status_code not in (200, 206):
                    raise MediaDownloadError(f'Unexpected HTTP status {response.status_code}')
                mime = response.headers.get('Content-Type', '').lower().split(';')[0].strip()
                if mime in ('text/html', 'application/json', 'text/plain', 'application/xhtml+xml'):
                    raise MediaDownloadError('Media URL returned HTML/JSON/text rather than video')
                if offset and response.status_code == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if not content_range.startswith(f'bytes {offset}-'):
                        raise MediaDownloadError('Server returned inconsistent Range response')
                if offset and response.status_code == 200:
                    # Server ignored Range; discard only this downloader's own .part.
                    offset = 0
                length = response.headers.get('Content-Length', '')
                if length.isdigit() and offset + int(length) > max_bytes:
                    raise MediaDownloadError('Download exceeds configured maximum size')
                total = offset
                with part.open('ab' if offset else 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024 * 512):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise MediaDownloadError('Download exceeded configured maximum size')
                        f.write(chunk)
                if not total:
                    raise MediaDownloadError('Media download returned zero bytes')
                # Reject obvious login/error page even if server lied about MIME.
                with part.open('rb') as f:
                    peek = f.read(128).lstrip().lower()
                if peek.startswith((b'<!doctype html', b'<html', b'{"error"', b'<?xml')):
                    part.unlink(missing_ok=True)
                    raise MediaDownloadError('Response body was an HTML/API error page, not a video')
                part.replace(output)
                return {'status': 'downloaded', 'file': output.name, 'bytes': total}
        raise MediaDownloadError('Too many redirects; download stopped')
    finally:
        if owned_session:
            session.close()


def public_report(candidates: list[MediaCandidate], events: list[dict]) -> dict:
    return {'version': '1.3', 'sources': [c.public_info() for c in candidates],
            'results': events, 'note': 'No raw video URLs, cookies or access tokens are stored.'}


def save_public_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use the same file for subsequent runs, but never output secret fields.
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    try:
        path.chmod(0o600)
    except OSError:
        pass
