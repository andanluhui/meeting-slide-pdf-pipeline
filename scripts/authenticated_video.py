# -*- coding: utf-8 -*-
"""User-authorized browser/HAR media capture -> resumable video -> slide PDF.

No password handling, DRM bypass, browser stealth, private browser profile scraping,
or automatic elevation of site permissions. The user signs in normally.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
import subprocess

from media_sources import (AuthenticationExpired, CaptureBuffer,
                           MediaDownloadError, collect_from_har, download_media,
                           host_allowed, source_id, public_report,
                           safe_filename, save_public_report)


def _page_urls(path):
    if not path:
        return []
    urls = [s.strip() for s in Path(path).read_text(encoding='utf-8-sig').splitlines()
            if s.strip() and not s.lstrip().startswith('#')]
    for url in urls:
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            raise ValueError('Page list must contain valid HTTP(S) URLs without embedded credentials')
    return urls


def _listen_while_user_works(page, message):
    """Pump Playwright events while a separate daemon waits for terminal input."""
    done = threading.Event()
    def terminal_wait():
        try:
            input(message)
        except EOFError:
            pass
        finally:
            done.set()
    threading.Thread(target=terminal_wait, daemon=True).start()
    while not done.is_set():
        page.wait_for_timeout(250)


def collect_from_browser(args):
    """Attach to *new* profile under work dir; user completes login in UI."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('Install browser adapter with: pip install playwright && playwright install chromium') from None

    profile = Path(args.profile_dir) if args.profile_dir else args.out / 'private_browser_profile'
    profile.mkdir(parents=True, exist_ok=True)
    try:
        profile.chmod(0o700)
    except OSError:
        pass
    print('Browser login profile stored locally at:', profile)
    print('Never commit or share this profile. Log in through the website normally.')
    capture = CaptureBuffer()
    with sync_playwright() as playwright:
        try:
            launch = {'headless': args.headless, 'accept_downloads': False}
            if args.browser_executable:
                launch['executable_path'] = args.browser_executable
            context = playwright.chromium.launch_persistent_context(str(profile), **launch)
        except Exception as exc:
            raise RuntimeError('Could not start browser. Run: python -m playwright install chromium') from None
        try:
            def on_response(response):
                try:
                    if response.request.method.upper() != 'GET':
                        return
                    mime = response.headers.get('content-type', '')
                    # Only inspect in-memory metadata for media responses.
                    if not capture.add(response.url, mime):
                        return
                    item = capture.candidates[source_id(response.url)]
                    headers = response.request.all_headers()
                    for key in ('authorization', 'cookie', 'referer', 'user-agent'):
                        if headers.get(key) and not item.headers.get(key):
                            item.headers[key] = headers[key]
                except Exception:
                    # Some service-worker/closed-page requests are not inspectable.
                    pass
            context.on('response', on_response)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until='domcontentloaded', timeout=60000)
            if args.pages_file:
                _listen_while_user_works(page, 'Log in through the browser. Press Enter to visit the supplied video pages... ')
                for link in _page_urls(args.pages_file):
                    try:
                        page.goto(link, wait_until='domcontentloaded', timeout=60000)
                        # Use the site's normal HTML video playback API; never bypass access checks.
                        page.wait_for_timeout(1000)
                        page.evaluate("() => document.querySelectorAll('video').forEach(v => { v.muted = true; v.play().catch(() => {}); })")
                        page.wait_for_timeout(int(args.visit_seconds * 1000))
                    except Exception:
                        print('One video page did not load. Continue with other authorized pages.')
                _listen_while_user_works(page, 'If necessary, play videos manually in this browser. Press Enter to finish capture... ')
            else:
                _listen_while_user_works(page, 'Log in, open the videos and press Play. Press Enter to finish capture... ')
            # Cookie values and captured headers are kept in memory only; never saved.
            for item in capture.list():
                try:
                    cookies = context.cookies([item.url])
                except Exception:
                    continue
                if cookies and not item.headers.get('cookie'):
                    item.headers['cookie'] = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
            return capture
        finally:
            context.close()


def _process_video(video: Path, output: Path, args) -> dict:
    pdf = output / (video.stem + '-slides.pdf')
    if pdf.is_file() and pdf.stat().st_size:
        return {'status': 'skipped', 'pdf': pdf.relative_to(args.out).as_posix()}
    cmd = [sys.executable, str(Path(__file__).parent / 'video_to_pdf.py'),
           str(video), '--output', str(pdf), '--fps', str(args.fps),
           '--layout', args.layout, '--min-free-gb', str(args.min_free_gb)]
    if args.auto_chapters:
        cmd.append('--auto-chapters')
    proc = subprocess.run(cmd, capture_output=True, timeout=8 * 3600)
    if proc.returncode:
        # Subprocess stdout/stderr may contain internal paths; don't persist to report.
        return {'status': 'failed', 'reason': 'PDF extraction failed; run video_to_pdf.py on the local video for diagnostics'}
    return {'status': 'created', 'pdf': pdf.relative_to(args.out).as_posix()}


def run(args):
    args.out = Path(args.out).expanduser().resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == 'har':
        capture = collect_from_har(args.har)
    else:
        capture = collect_from_browser(args)
    found = capture.list()
    print('Observed media hosts and candidate counts:')
    for host, count in capture.hosts().items():
        print(' ', host, count)
    if not found:
        print('No media requests detected. Start playback first; streaming sites may require a separate adapter.')
        return 2
    allowed = args.allow_host or []
    if not allowed and sys.stdin.isatty():
        response = input('Enter approved media hostnames, separated by commas (blank = inspect only): ')
        allowed = [x.strip() for x in response.split(',') if x.strip()]
    chosen = [x for x in found if host_allowed(x.host, allowed)]
    if not chosen:
        print('No approved media hosts. No downloads attempted.')
        return 0
    if args.dry_run:
        print('Dry-run: %d allowed resources; no downloads or PDF processing.' % len(chosen))
        return 0
    videos = args.out / 'videos'
    pdfs = args.out / 'pdf'
    pdfs.mkdir(parents=True, exist_ok=True)
    log_file = args.out / 'media-report.json'
    events = []
    errors = 0
    for candidate in chosen[:args.max_items]:
        public = candidate.public_info()
        if candidate.kind == 'playlist':
            events.append({**public, 'status': 'unsupported',
                           'reason': 'HLS/DASH manifests require a separately authorized stream adapter'})
            print(candidate.host, candidate.key, 'playlist not supported')
            continue
        suffix = candidate.kind if candidate.kind in ('mp4','webm','mkv') else 'mp4'
        video = videos / safe_filename(candidate.title, candidate.key, suffix)
        try:
            status = download_media(candidate, video, allowed,
                                    allow_private=args.allow_private_hosts,
                                    max_bytes=int(args.max_download_gb * 1024**3))
            event = {**public, 'status': status['status'],
                     'video': video.relative_to(args.out).as_posix(),
                     'bytes': status['bytes']}
            print(candidate.host, candidate.key, status['status'], status['bytes'], 'bytes')
            if not args.download_only:
                event['processing'] = _process_video(video, pdfs, args)
                if event['processing']['status'] == 'failed':
                    errors += 1
        except AuthenticationExpired as exc:
            errors += 1
            event = {**public, 'status': 'authentication_required', 'reason': str(exc)}
            print(candidate.host, candidate.key, 'login expired; re-login/replay and rerun')
        except (MediaDownloadError, OSError) as exc:
            errors += 1
            event = {**public, 'status': 'failed', 'reason': str(exc)}
            print(candidate.host, candidate.key, 'failed:', str(exc))
        events.append(event)
        save_public_report(log_file, public_report(chosen, events))
    print('Processed', len(events), 'candidates; failures:', errors)
    print('Credential-free report:', log_file)
    return 1 if errors else 0


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('har', 'browser'):
        s = sub.add_parser(command)
        if command == 'har':
            s.add_argument('--har', required=True, type=Path,
                           help='Sensitive HAR exported from your own authorized browser session')
        else:
            s.add_argument('--url', required=True, help='Authorized login/video portal page')
            s.add_argument('--profile-dir', help='Private local browser profile; never upload it')
            s.add_argument('--headless', action='store_true', help='Headless for testing/previously authenticated profiles; fresh login usually needs visible browser')
            s.add_argument('--browser-executable', help='Optional system Chromium executable instead of Playwright bundled browser')
            s.add_argument('--pages-file', help='Optional local text list of authorized session pages')
            s.add_argument('--visit-seconds', type=float, default=10.0,
                           help='Seconds to observe each supplied page (default 10)')
        s.add_argument('--out', default='./video-jobs', help='Local private work directory')
        s.add_argument('--allow-host', action='append', default=[],
                       help='Explicitly approve a media host, e.g. media.example.org; repeat as needed')
        s.add_argument('--allow-private-hosts', action='store_true',
                       help='Opt into authorized localhost/private network host downloads')
        s.add_argument('--dry-run', action='store_true')
        s.add_argument('--download-only', action='store_true',
                       help='Do not process the downloaded video into PDF')
        s.add_argument('--max-items', type=int, default=200)
        s.add_argument('--max-download-gb', type=float, default=8)
        s.add_argument('--fps', type=float, default=2.0)
        s.add_argument('--layout', choices=['auto','full','blackbars'], default='auto')
        s.add_argument('--auto-chapters', action='store_true')
        s.add_argument('--min-free-gb', type=float, default=1.0)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.max_items < 1 or args.max_download_gb <= 0 or (args.command == 'browser' and args.visit_seconds <= 0):
        print('Invalid max-items, max-download-gb or visit-seconds')
        return 2
    try:
        return run(args)
    except (ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
        # Avoid accidental token leak from framework exceptions. Print only sanitized classes.
        if isinstance(exc, (FileNotFoundError, ValueError)):
            print('Invalid input or missing local file; check the local HAR/page list.')
        else:
            print('Could not initialize authenticated capture; check browser/runtime prerequisites.')
        return 2


if __name__ == '__main__':
    sys.exit(main())
