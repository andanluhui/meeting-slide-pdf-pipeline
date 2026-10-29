# -*- coding: utf-8 -*-
"""Explicit, optional Feishu Drive upload of ordinary PDF files (no spreadsheet).

Examples:
  python scripts/upload_pdf.py output/slides.pdf --dry-run
  LARK_FOLDER_TOKEN=... LARK_FILE_URL_PREFIX=https://tenant.feishu.cn/file/ \
      python scripts/upload_pdf.py output/slides.pdf

Requires a separately authenticated lark-cli. This script never writes a sheet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def file_hash(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for part in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def get_log(path: Path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_log(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + '.incomplete')
    try:
        staging.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        staging.replace(path)
    finally:
        if staging.exists():
            staging.unlink()


def upload(cli: str, folder_token: str, path: Path) -> str:
    try:
        r = subprocess.run([cli, 'drive', '+upload', '--file', str(path),
                            '--name', path.name, '--folder-token', folder_token,
                            '--format', 'json'], capture_output=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Feishu CLI could not run; check installation and login') from exc
    if r.returncode:
        # Never print raw stderr: it may contain private file URLs or tokens.
        raise RuntimeError('Feishu CLI upload returned error status %s' % r.returncode)
    try:
        value = json.loads(r.stdout.decode('utf-8', errors='replace'))
        token = value.get('data', {}).get('file_token') if value.get('ok') else None
    except (ValueError, AttributeError):
        token = None
    if not token or not isinstance(token, str):
        raise RuntimeError('Feishu upload did not return a usable file token')
    return token


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pdf', nargs='+', help='PDF file(s) to upload')
    ap.add_argument('--dry-run', action='store_true', help='Show work without uploading')
    ap.add_argument('--folder-token', default=os.getenv('LARK_FOLDER_TOKEN'))
    ap.add_argument('--file-url-prefix', default=os.getenv('LARK_FILE_URL_PREFIX'))
    ap.add_argument('--lark-cli', default=os.getenv('LARK_CLI') or shutil.which('lark-cli') or 'lark-cli')
    ap.add_argument('--log', help='Private local cache file path (default: next to PDF)')
    ap.add_argument('--force', action='store_true', help='Upload again even if identical content was uploaded')
    a = ap.parse_args(argv)
    if not a.dry_run and (not a.folder_token or not a.file_url_prefix):
        ap.error('Set LARK_FOLDER_TOKEN and LARK_FILE_URL_PREFIX (or equivalent flags) to upload')
    if a.file_url_prefix and not a.file_url_prefix.startswith('https://'):
        ap.error('LARK_FILE_URL_PREFIX must begin with https://')
    paths = [Path(s).expanduser().resolve() for s in a.pdf]
    if any(not p.is_file() or p.suffix.lower() != '.pdf' for p in paths):
        ap.error('All input files must exist and have .pdf extension')
    logpath = Path(a.log).expanduser().resolve() if a.log else paths[0].parent / '.pdf_uploads.json'
    cache = get_log(logpath)
    scope = hashlib.sha256((a.folder_token or '').encode('utf-8')).hexdigest()[:16]
    for p in paths:
        if a.dry_run:
            print('[DRY] Would upload:', p.name)
            continue
        content_sha = file_hash(p)
        key = scope + ':' + content_sha
        if key in cache and not a.force:
            print('[REUSED]', p.name, cache[key]['url'])
            continue
        tok = upload(a.lark_cli, a.folder_token, p)
        url = a.file_url_prefix.rstrip('/') + '/' + tok
        cache[key] = {'name': p.name, 'sha256': content_sha, 'url': url}
        save_log(logpath, cache)
        print('[UPLOADED]', p.name, url)
    if a.dry_run:
        print('No files were uploaded.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as e:
        print('ERROR:', str(e), file=sys.stderr)
        sys.exit(1)
