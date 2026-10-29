# -*- coding: utf-8 -*-
"""Use a verified, authorized video request as a pattern for supplied session IDs.

Workflow: play one video in your browser -> export HAR -> supply a list of
sessions you are authorized to access -> inspect -> download -> slide PDFs.

The script never scans or guesses session IDs and does not bypass login or DRM.
Signed links are not guaranteed to work with substituted IDs; authentication
failures stop the batch so the user can return to ordinary browser capture.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from authenticated_video import _process_video
from media_sources import (
    AuthenticationExpired, MediaDownloadError, collect_from_har, download_media,
    host_allowed, make_candidate, public_report, safe_filename, save_public_report,
)

ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
COMMON_PATH_MARKERS = ('session', 'sessions', 'lecture', 'lectures', 'talk', 'talks', 'recording', 'recordings')


def read_ids(path: Path, limit: int = 300) -> list[str]:
    """Only use explicit, user-supplied, authorized IDs; never enumerate."""
    path = Path(path).expanduser()
    if path.stat().st_size > 100_000:
        raise ValueError('Session list is too large')
    values = []
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or 'id' not in {x.lower().strip() for x in reader.fieldnames}:
                raise ValueError('CSV needs a column named id')
            key = next(x for x in reader.fieldnames if x.lower().strip() == 'id')
            values = [(row.get(key) or '').strip() for row in reader]
    else:
        values = [line.strip() for line in path.read_text(encoding='utf-8-sig').splitlines()
                  if line.strip() and not line.lstrip().startswith('#')]
    if not values or len(values) > limit:
        raise ValueError('Session list is empty or exceeds the permitted count')
    for value in values:
        if not ID_RE.fullmatch(value):
            raise ValueError('Session IDs must contain only letters, numbers, underscore or hyphen')
    # Keep order; reject duplicates so the download report is unambiguous.
    if len(set(values)) != len(values):
        raise ValueError('Duplicate IDs in the session list')
    return values


def find_template(candidates, sample_id=None):
    """Select only a network-observed video; locate one complete path segment to replace.

    If a HAR contains more than one plausible source, require --example-id.
    """
    options = []
    for candidate in candidates:
        if candidate.kind not in ('mp4', 'mkv', 'webm'):
            continue
        parts = urlsplit(candidate.url)
        segments = parts.path.split('/')
        matches = []
        if sample_id:
            matches = [(i, value) for i, value in enumerate(segments)
                       if value == sample_id and ID_RE.fullmatch(value)]
        else:
            for i in range(1, len(segments) - 1):
                if segments[i-1].lower() in COMMON_PATH_MARKERS and ID_RE.fullmatch(segments[i]):
                    matches.append((i, segments[i]))
        if len(matches) == 1:
            options.append((candidate, matches[0][0], matches[0][1]))
    if len(options) != 1:
        raise ValueError('Could not uniquely identify the sample video. Use --example-id after playing exactly one example.')
    return options[0]


def build_candidates(observed, part_index: int, original_id: str, ids: list[str]):
    parts = urlsplit(observed.url)
    segments = parts.path.split('/')
    if not 0 <= part_index < len(segments) or segments[part_index] != original_id:
        raise ValueError('Observed sample video does not match the selected session ID')
    items = []
    for session_id in ids:
        changed = list(segments)
        changed[part_index] = session_id
        target = urlunsplit((parts.scheme, parts.netloc, '/'.join(changed), parts.query, parts.fragment))
        # Use the original request's authorization material only transiently.
        candidate = make_candidate(target, observed.content_type, observed.headers,
                                   title=f'session-{session_id}')
        if candidate is None or candidate.host != observed.host or candidate.kind != observed.kind:
            raise ValueError('Candidate URL no longer matches the observed video type or host')
        items.append(candidate)
    return items


def run(args):
    if not args.allow_host:
        raise ValueError('The video server hostname must be approved with --allow-host')
    ids = read_ids(args.ids_file, args.max_items)
    observed = collect_from_har(args.har).list()
    sample, part_index, original_id = find_template(observed, args.example_id)
    if not host_allowed(sample.host, args.allow_host):
        raise ValueError('The example video host is not included in --allow-host')
    candidates = build_candidates(sample, part_index, original_id, ids)
    print('已找到一个实际播放过的视频地址。')
    print('视频服务器：', sample.host)
    print('用户提供的场次：', len(candidates))
    print('将只替换场次编号，其余视频地址部分保持不变。')
    print('提醒：如果网站的下载许可或链接与每场单独绑定，其他场次可能无法下载。')
    if args.dry_run:
        print('预览完成：没有下载任何视频，也没有生成 PDF。')
        return 0

    args.out = Path(args.out).expanduser().resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    video_dir, pdf_dir = args.out/'videos', args.out/'pdf'
    video_dir.mkdir(parents=True, exist_ok=True)
    if not args.download_only:
        pdf_dir.mkdir(parents=True, exist_ok=True)
    report = args.out/'media-report.json'
    events = []
    failed = False
    for index, (session_id, candidate) in enumerate(zip(ids, candidates), start=1):
        filename = safe_filename(candidate.title, candidate.key, candidate.kind)
        video_file = video_dir/filename
        info = candidate.public_info()
        try:
            result = download_media(candidate, video_file, args.allow_host,
                                    allow_private=args.allow_private_hosts,
                                    max_bytes=int(args.max_download_gb * 1024**3))
            event = {**info, 'status': result['status'],
                     'video': video_file.relative_to(args.out).as_posix(), 'bytes': result['bytes']}
            print(f'[{index}/{len(ids)}] {session_id}: {result["status"]}')
            if not args.download_only:
                event['processing'] = _process_video(video_file, pdf_dir, args)
                if event['processing']['status'] == 'failed':
                    failed = True
        except AuthenticationExpired:
            event = {**info, 'status': 'authentication_required',
                     'reason': 'Login or media permission expired; play this session normally in the browser'}
            events.append(event)
            save_public_report(report, public_report(candidates, events))
            print('网站拒绝了访问；已停止批量任务。请重新登录，或改用逐场网页捕获。')
            return 1
        except (MediaDownloadError, OSError) as error:
            # Never print raw signed URL or HTTP request headers.
            event = {**info, 'status': 'failed', 'reason': str(error)}
            failed = True
            print(f'[{index}/{len(ids)}] {session_id}: failed ({type(error).__name__})')
        events.append(event)
        save_public_report(report, public_report(candidates, events))
        if args.delay and index < len(candidates):
            time.sleep(args.delay)
    print('任务结束：报告保存在输出目录，原始登录信息不会写入报告。')
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Use an observed authorized video address to download a user-supplied list of sessions.')
    parser.add_argument('--har', type=Path, required=True,
                        help='Local private HAR exported after actually playing one video')
    parser.add_argument('--ids-file', type=Path, required=True,
                        help='One authorized session ID per line (.txt) or an id column (.csv)')
    parser.add_argument('--example-id', help='ID of the played sample (use if the pattern is ambiguous)')
    parser.add_argument('--allow-host', action='append', required=True,
                        help='Explicit approval of video host, obtained with the HAR preview')
    parser.add_argument('--allow-private-hosts', action='store_true',
                        help='Only for authorized local/private test servers')
    parser.add_argument('--out', default='./video-jobs')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, without downloading')
    parser.add_argument('--download-only', action='store_true', help='Do not create PDFs')
    parser.add_argument('--max-items', type=int, default=300)
    parser.add_argument('--max-download-gb', type=float, default=8.0)
    parser.add_argument('--delay', type=float, default=0.5, help='Seconds to pause between requests')
    parser.add_argument('--fps', type=float, default=2.0)
    parser.add_argument('--layout', choices=('auto', 'full', 'blackbars'), default='auto')
    parser.add_argument('--auto-chapters', action='store_true')
    parser.add_argument('--min-free-gb', type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.max_items < 1 or args.max_items > 3000 or args.max_download_gb <= 0 or args.delay < 0:
        parser.error('Invalid max-items, max-download-gb or delay')
    try:
        return run(args)
    except (OSError, ValueError, json.JSONDecodeError):
        # Neither HAR URLs nor sensitive cookies should appear in error output.
        print('没有找到唯一的视频地址规律。请检查 HAR 文件、场次编号文件，必要时指定 --example-id。')
        return 2


if __name__ == '__main__':
    sys.exit(main())
