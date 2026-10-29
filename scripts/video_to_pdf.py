# -*- coding: utf-8 -*-
"""Generic, local-first video -> deduplicated slide PDF (no Feishu or agenda).

Examples:
  python scripts/video_to_pdf.py ./lecture.mp4
  python scripts/video_to_pdf.py 'https://example.org/lecture.mp4' --output ./lecture.pdf
  python scripts/video_to_pdf.py ./lecture.mov --crop 0.0,0.0,0.8,1.0
  python scripts/video_to_pdf.py ./conference.mp4 --chapters chapters.json

Only direct, authorized HTTP(S) video resources are supported. Web-page players,
protected streams and login flows need a separate source adapter; never bypass DRM.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def clean_title(title: str, limit: int = 85) -> str:
    """Keep user-supplied filenames portable across Windows/macOS/Linux."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(title))
    text = text.strip(' .')[:limit].strip(' .')
    return text or 'slides'


def source_kind(source: str) -> str:
    parsed = urlsplit(source)
    if parsed.scheme.lower() in ('http', 'https') and parsed.netloc:
        if parsed.username or parsed.password:
            raise ValueError('Do not embed credentials in video URLs; use an authorized direct link.')
        return 'url'
    if parsed.scheme and parsed.scheme.lower() not in ('http', 'https') and '://' in source:
        raise ValueError('Only local files and direct HTTP(S) URLs are supported.')
    return 'local'


def default_output(source: str) -> Path:
    if source_kind(source) == 'url':
        parsed = urlsplit(source)
        name = Path(unquote(parsed.path)).stem or 'online-video'
        # Distinguish URLs with the same last path component without exposing tokens.
        short_id = hashlib.sha256(source.encode('utf-8')).hexdigest()[:8]
        return Path.cwd() / (clean_title(name, 56) + '-' + short_id + '-slides.pdf')
    return Path.cwd() / (clean_title(Path(source).stem, 56) + '-slides.pdf')


def check_disk(directory: Path, minimum_gb: float) -> None:
    free = shutil.disk_usage(directory).free / (1024 ** 3)
    if free < minimum_gb:
        raise RuntimeError('Insufficient free space (%.2f GB, required %.2f GB): %s' %
                           (free, minimum_gb, directory))


@contextmanager
def temporary_workspace(parent: Path, keep: bool = False):
    parent.mkdir(parents=True, exist_ok=True)
    if keep:
        work = Path(tempfile.mkdtemp(prefix='slide-work-', dir=parent))
        try:
            yield work
        finally:
            print('Retained intermediate files:', work)
    else:
        with tempfile.TemporaryDirectory(prefix='slide-work-', dir=parent) as d:
            yield Path(d)


def get_video(source: str, work: Path, max_download_gb: float) -> Path:
    """Read a local video untouched, or download a direct video response to work/."""
    if source_kind(source) == 'local':
        p = Path(source).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError('Source video not found: %s' % p)
        if not p.stat().st_size:
            raise ValueError('Source video is empty')
        return p

    import requests
    limit = int(max_download_gb * 1024 ** 3)
    if limit <= 0:
        raise ValueError('--max-download-gb must be greater than zero')
    dst = work / 'downloaded-video.mp4'
    part = work / 'downloaded-video.part'
    written = 0
    # Never print the URL or response body: they can contain access tokens.
    try:
        response_handle = requests.get(source, stream=True, allow_redirects=True,
                      headers={'Accept': 'video/*,application/octet-stream;q=0.9,*/*;q=0.5',
                               'User-Agent': 'meeting-slide-pdf-pipeline/1.1'},
                      timeout=(20, 120))
    except requests.RequestException as exc:
        raise RuntimeError('Video download failed before receiving a response') from exc
    with response_handle as response:
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError('Video download returned HTTP %s' % response.status_code) from exc
        ctype = response.headers.get('Content-Type', '').lower().split(';')[0].strip()
        if ctype in ('text/html', 'application/xhtml+xml', 'application/json', 'text/plain'):
            raise ValueError('URL returned a web page or API response, not a direct video file.')
        size = response.headers.get('Content-Length')
        if size and size.isdigit() and int(size) > limit:
            raise ValueError('Video exceeds --max-download-gb limit.')
        with open(part, 'wb') as out:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    raise ValueError('Video exceeded download size limit; discarded partial download.')
                out.write(chunk)
    if not written:
        raise ValueError('Video response was empty')
    part.replace(dst)
    return dst


def ffmpeg_executable():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        result = shutil.which('ffmpeg')
        if not result:
            raise RuntimeError('FFmpeg is not available; install imageio-ffmpeg.')
        return result


def extract_frames(video: Path, work: Path, fps: float, max_frames: int):
    """Sample real decoded frames; do not use keyframe-only extraction."""
    if fps <= 0 or fps > 5:
        raise ValueError('--fps must be within (0, 5]')
    frame_dir = work / 'frames'
    frame_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(frame_dir / 'frame_%06d.jpg')
    cmd = [ffmpeg_executable(), '-nostdin', '-hide_banner', '-loglevel', 'error',
           '-i', str(video), '-map', '0:v:0', '-an', '-vf', 'fps=%.6g' % fps,
           '-q:v', '3', '-frames:v', str(max_frames + 1), '-start_number', '0',
           '-y', pattern]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=8 * 3600)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Video frame extraction exceeded the time limit') from exc
    if result.returncode:
        msg = result.stderr.decode('utf-8', errors='replace')[-600:]
        raise RuntimeError('FFmpeg could not decode the supplied video: ' + msg)
    frames = sorted(frame_dir.glob('frame_*.jpg'))
    if len(frames) > max_frames:
        raise ValueError('Video exceeds --max-frames limit; raise the limit explicitly.')
    if not frames:
        raise ValueError('No video frames could be decoded')
    return [(n / fps, p) for n, p in enumerate(frames)]


def parse_crop(value: str | None, width: int, height: int):
    """(x,y,width,height) as normalized [0,1] or absolute pixel coordinates."""
    if value is None:
        return (0, 0, width, height)
    try:
        vals = [float(x.strip()) for x in value.split(',')]
    except ValueError as exc:
        raise ValueError('--crop needs x,y,width,height') from exc
    if len(vals) != 4 or min(vals) < 0:
        raise ValueError('--crop needs four non-negative numbers')
    x, y, w, h = vals
    if all(v <= 1 for v in vals):
        x, w = x * width, w * width
        y, h = y * height, h * height
    x1, y1 = int(round(x)), int(round(y))
    x2, y2 = int(round(x + w)), int(round(y + h))
    if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x2 - x1 < 32 or y2 - y1 < 32:
        raise ValueError('--crop is outside the image or too small')
    return (x1, y1, x2, y2)


def auto_crop(frames):
    """Conservative aspect-independent removal of persistent *black* letterbox bars.

    Do not try to guess a nested slide rectangle: a wrong crop can destroy slides.
    Manual --crop overrides this; otherwise retain full frame if uncertain.
    """
    import numpy as np
    from PIL import Image
    samples = [frames[i][1] for i in sorted(set((0, len(frames)//2, len(frames)-1)))]
    arrays = [np.asarray(Image.open(f).convert('RGB')) for f in samples]
    h, w = arrays[0].shape[:2]
    dark_lines = []
    for arr in arrays:
        dark = arr.max(axis=2) < 25
        dark_lines.append((np.mean(dark, axis=1) > 0.985,
                           np.mean(dark, axis=0) > 0.985))
    rows = np.logical_and.reduce([x[0] for x in dark_lines])
    cols = np.logical_and.reduce([x[1] for x in dark_lines])
    top = next((i for i in range(h) if not rows[i]), h)
    bottom = next((i for i in range(h-1, -1, -1) if not rows[i]), -1) + 1
    left = next((i for i in range(w) if not cols[i]), w)
    right = next((i for i in range(w-1, -1, -1) if not cols[i]), -1) + 1
    if (bottom - top < h * 0.75 or right - left < w * 0.75 or
            (left == 0 and top == 0 and right == w and bottom == h)):
        return (0, 0, w, h)
    # Reject barely-detected edges; these could just be a dark slide design.
    if max(left, top, w-right, h-bottom) < max(3, min(w,h)*0.01):
        return (0, 0, w, h)
    return (left, top, right, bottom)


def signature(frame: Path, crop):
    """Low-cost foreground representation, valid on light OR dark slides."""
    import numpy as np
    from PIL import Image
    with Image.open(frame) as src:
        im = src.convert('RGB').crop(crop).resize((320, 180), Image.Resampling.BILINEAR)
    rgb = np.asarray(im, dtype=np.uint8)
    pix = rgb.astype(np.int16)
    band = np.concatenate((pix[:8].reshape(-1, 3), pix[-8:].reshape(-1, 3),
                           pix[:, :8].reshape(-1, 3), pix[:, -8:].reshape(-1, 3)))
    bg = np.median(band, axis=0)
    foreground = np.max(np.abs(pix - bg), axis=2) > 34
    # Edge map supplements the foreground mask for gradient/photo backgrounds.
    gray = (pix[:, :, 0] * 0.299 + pix[:, :, 1] * 0.587 + pix[:, :, 2] * 0.114)
    edges = np.zeros(gray.shape, dtype=bool)
    edges[:, 1:] |= np.abs(np.diff(gray, axis=1)) > 19
    edges[1:, :] |= np.abs(np.diff(gray, axis=0)) > 19
    return rgb, np.logical_or(foreground, edges)


def is_blank(rgb, fg):
    import numpy as np
    # Avoid keeping a black/white transition frame, but keep minimalist slides.
    return float(np.std(rgb)) < 4.5 and float(np.mean(fg)) < 0.002


def is_new_page(old, new, threshold: float = 0.025):
    import numpy as np
    old_rgb, old_fg = old
    new_rgb, new_fg = new
    delta = np.abs(old_rgb.astype(np.int16) - new_rgb.astype(np.int16))
    changed = float(np.mean(np.max(delta, axis=2) > 27))
    magnitude = float(np.mean(delta))
    removed = float(np.count_nonzero(old_fg & ~new_fg) / max(1, np.count_nonzero(old_fg)))
    # Large all-frame changes (including white<->black slides).
    if changed > 0.23 and magnitude > 17:
        return True
    # Slides reusing the same template but replacing text/figures.
    if removed > threshold and changed > 0.003:
        return True
    return False


def choose_pages(frames, crop, dedup_threshold):
    """Keep last frame of a gradual reveal. Never discard a one-page lecture."""
    pages = []
    current = None
    current_sig = None
    for t, frame in frames:
        s = signature(frame, crop)
        if is_blank(*s):
            continue
        if current is None:
            current = {'start_sec': round(t, 2), 'last_sec': round(t, 2),
                       'frame': str(frame)}
            current_sig = s
        elif is_new_page(current_sig, s, dedup_threshold):
            pages.append(current)
            current = {'start_sec': round(t, 2), 'last_sec': round(t, 2),
                       'frame': str(frame)}
            current_sig = s
        else:
            current['last_sec'] = round(t, 2)
            current['frame'] = str(frame)
            # Replace reference with latest (never OR masks together). This keeps
            # the finished reveal available for detecting subsequent replacement.
            current_sig = s
    if current:
        pages.append(current)
    return pages


def save_pdf(pages, crop, output: Path, *, overwrite: bool):
    import io
    from PIL import Image
    import pymupdf
    if output.exists() and not overwrite:
        raise FileExistsError('Output exists. Use --overwrite to replace: %s' % output)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    try:
        for p in pages:
            with Image.open(p['frame']) as src:
                im = src.convert('RGB').crop(crop)
            buffer = io.BytesIO()
            im.save(buffer, format='JPEG', quality=90)
            page = doc.new_page(width=im.width, height=im.height)
            page.insert_image(page.rect, stream=buffer.getvalue())
        # Write via staging path to protect existing output on failure.
        tmp = output.with_name(output.name + '.incomplete')
        try:
            doc.save(str(tmp), garbage=3, deflate=True)
            tmp.replace(output)
        finally:
            if tmp.exists():
                tmp.unlink()
    finally:
        doc.close()


def read_chapters(filename: str):
    with open(filename, encoding='utf-8-sig') as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise ValueError('--chapters must be a nonempty JSON list')
    out = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict) or 'start' not in entry or 'title' not in entry:
            raise ValueError('Chapter %d needs start and title' % (i + 1))
        start = float(entry['start'])
        if start < 0 or (out and start <= out[-1]['start']):
            raise ValueError('Chapters must have non-negative, strictly increasing start times')
        title = clean_title(str(entry['title']))
        out.append({'start': start, 'title': title})
    return out


def build_chapters(pages, chapters, crop, output: Path, overwrite: bool):
    folder = output.parent / (output.stem + '-chapters')
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for i, chapter in enumerate(chapters):
        stop = chapters[i+1]['start'] if i + 1 < len(chapters) else float('inf')
        selected = [p for p in pages if chapter['start'] <= p['start_sec'] < stop]
        if not selected:
            results.append({'title': chapter['title'], 'pages': 0, 'status': 'empty'})
            continue
        dst = folder / ('%02d_%s.pdf' % (i+1, chapter['title']))
        save_pdf(selected, crop, dst, overwrite=overwrite)
        results.append({'title': chapter['title'], 'pages': len(selected),
                        'path': str(dst), 'status': 'created'})
    return results


def save_contact_sheet(pages, crop, dst: Path, overwrite: bool):
    from PIL import Image, ImageDraw
    if dst.exists() and not overwrite:
        raise FileExistsError('Contact sheet exists. Use --overwrite: %s' % dst)
    show = pages[:120]
    width, height, cols, label = 210, 130, 4, 22
    rows = (len(show) + cols - 1) // cols
    sheet = Image.new('RGB', (cols*width, rows*(height+label)), 'white')
    drawer = ImageDraw.Draw(sheet)
    for i, p in enumerate(show):
        x, y = (i % cols)*width, (i // cols)*(height+label)
        with Image.open(p['frame']) as src:
            img = src.convert('RGB').crop(crop)
        img.thumbnail((width-8, height-8))
        sheet.paste(img, (x + (width-img.width)//2, y + (height-img.height)//2))
        drawer.text((x+5, y+height), '%d  %.1fs' % (i+1, p['start_sec']), fill='black')
    sheet.save(dst, quality=88)


def preflight(outputs, overwrite: bool):
    if overwrite:
        return
    existing = [p for p in outputs if p.exists()]
    if existing:
        raise FileExistsError('Output already exists: %s (use --overwrite)' % existing[0])


def run(args):
    output = Path(args.output).expanduser().resolve() if args.output else default_output(args.source).resolve()
    if output.suffix.lower() != '.pdf':
        raise ValueError('--output must end in .pdf')
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = output.with_suffix('.json')
    contact_path = output.with_name(output.stem + '-contact.jpg')
    chapters = read_chapters(args.chapters) if args.chapters else []
    preflight([output, report_path, contact_path], args.overwrite)
    if chapters and not args.overwrite:
        preflight([output.parent / (output.stem+'-chapters') /
                   ('%02d_%s.pdf' % (i+1, c['title'])) for i,c in enumerate(chapters)], False)
    check_disk(output.parent, args.min_free_gb)
    with temporary_workspace(output.parent, args.keep_work) as work:
        video = get_video(args.source, work, args.max_download_gb)
        print('Input accepted (%s); decoding frames...' % source_kind(args.source))
        frames = extract_frames(video, work, args.fps, args.max_frames)
        from PIL import Image
        with Image.open(frames[0][1]) as im:
            w, h = im.size
        crop = parse_crop(args.crop, w, h) if args.crop else auto_crop(frames)
        pages = choose_pages(frames, crop, args.dedup_threshold)
        if not pages:
            raise ValueError('No non-blank slide-like frames found')
        print('Sampled %d frames; retained %d candidate pages; crop=%s' %
              (len(frames), len(pages), crop))
        save_pdf(pages, crop, output, overwrite=args.overwrite)
        save_contact_sheet(pages, crop, contact_path, overwrite=args.overwrite)
        chapter_results = build_chapters(pages, chapters, crop, output, args.overwrite) if chapters else []
        report = {'version': '1.1', 'input_type': source_kind(args.source),
                  'input_name': Path(urlsplit(args.source).path).name if source_kind(args.source) == 'url'
                  else Path(args.source).name,
                  'pdf': str(output), 'contact_sheet': str(contact_path),
                  'sampled_frames': len(frames), 'pages': len(pages),
                  'sampling_fps': args.fps, 'frame_size': [w,h], 'crop_xyxy': list(crop),
                  'page_times_sec': [p['start_sec'] for p in pages],
                  'chapters': chapter_results,
                  'note': 'Candidate slide boundaries need visual review for complex layouts.'}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PDF:', output)
        print('Contact sheet:', contact_path)
        print('Report:', report_path)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', help='Local video path or an authorized direct HTTP(S) video URL')
    parser.add_argument('--output', '-o', help='Output PDF path (default: current directory)')
    parser.add_argument('--fps', type=float, default=1.0, help='Frame samples per second (default: 1)')
    parser.add_argument('--crop', help='Manual x,y,width,height in fractions or pixels; otherwise detect black bars')
    parser.add_argument('--chapters', help='Optional JSON list of chapter {start,title} objects')
    parser.add_argument('--dedup-threshold', type=float, default=0.025,
                        help='Fraction of replaced foreground content for slide changes (default: .025)')
    parser.add_argument('--max-frames', type=int, default=21600,
                        help='Hard limit to avoid massive temp extraction (default 21600)')
    parser.add_argument('--max-download-gb', type=float, default=10.0)
    parser.add_argument('--min-free-gb', type=float, default=1.0)
    parser.add_argument('--keep-work', action='store_true', help='Retain extracted frames for diagnostics')
    parser.add_argument('--overwrite', action='store_true', help='Explicitly allow replacing existing outputs')
    args = parser.parse_args(argv)
    if args.fps <= 0 or args.fps > 5 or args.max_frames < 1 or not 0 < args.dedup_threshold < 1:
        parser.error('fps must be (0,5], max-frames >=1, and dedup-threshold must be (0,1)')
    try:
        import pybootstrap
        pybootstrap.ensure(('numpy', 'PIL', 'pymupdf', 'requests', 'imageio_ffmpeg'))
        run(args)
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError, OSError) as exc:
        # Errors should not leak video URLs with embedded access tokens.
        msg = str(exc).replace(args.source, '<redacted input>')
        print('ERROR:', msg, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
