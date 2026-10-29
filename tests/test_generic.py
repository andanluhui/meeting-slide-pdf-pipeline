# -*- coding: utf-8 -*-
"""Offline regression tests for the generic entrance; no Feishu credentials needed."""
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'scripts'))
import video_to_pdf as v
import upload_pdf as u


class BasicTests(unittest.TestCase):
    def test_input_and_cropping(self):
        self.assertEqual(v.source_kind('https://example.org/movie.mp4?token=x'), 'url')
        self.assertEqual(v.source_kind('./movie.mp4'), 'local')
        self.assertEqual(v.parse_crop('0.1,0,0.8,1', 1000, 500), (100, 0, 900, 500))
        self.assertEqual(v.parse_crop('20,10,100,100', 1000, 500), (20, 10, 120, 110))
        with self.assertRaises(ValueError):
            v.parse_crop('0,0,2,2', 1000, 500)
        with self.assertRaises(ValueError):
            v.source_kind('ftp://example.org/movie.mp4')

    def test_chapters_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'chapters.json'
            p.write_text(json.dumps([{'start': 0, 'title': 'Start'},
                                     {'start': 5, 'title': 'Middle'}]), encoding='utf-8')
            self.assertEqual(len(v.read_chapters(str(p))), 2)
            p.write_text(json.dumps([{'start': 10, 'title': 'Late'},
                                     {'start': 5, 'title': 'Early'}]), encoding='utf-8')
            with self.assertRaises(ValueError):
                v.read_chapters(str(p))

    def test_upload_idempotency_mocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'slides.pdf'
            p.write_bytes(b'%PDF-1.4\nexample')
            args = [str(p), '--folder-token', 'demo-folder',
                    '--file-url-prefix', 'https://example.feishu.cn/file/']
            with mock.patch.object(u, 'upload', return_value='fake-file-token') as send:
                self.assertEqual(u.main(args), 0)
                self.assertEqual(u.main(args), 0)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(u.main(args + ['--force']), 0)
                self.assertEqual(send.call_count, 2)
            cache = json.loads((Path(tmp) / '.pdf_uploads.json').read_text())
            self.assertEqual(len(cache), 1)
            self.assertNotIn('demo-folder', str(cache))

    def test_uploader_private_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'file.pdf'
            p.write_bytes(b'%PDF-1.4\nexample')
            self.assertEqual(u.main([str(p), '--dry-run']), 0)
            self.assertFalse((Path(tmp) / '.pdf_uploads.json').exists())


class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('ffmpeg'):
            raise unittest.SkipTest('Requires ffmpeg for integration test')
        try:
            from PIL import Image, ImageDraw
            import pymupdf
        except ImportError as e:
            raise unittest.SkipTest('Missing dependency: ' + str(e))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        movie_frames = cls.folder / 'movie_frames'
        movie_frames.mkdir()
        captions = [('Intro A', 'white'), ('Intro A', 'white'),
                    ('Intro A', 'white'), ('Intro A', 'white'),
                    ('Intro A and extra line', 'white'), ('Intro A and extra line', 'white'),
                    ('Totally different text 999', 'white'), ('Totally different text 999', 'white'),
                    ('Totally different text 999', 'white'), ('Totally different text 999', 'white'),
                    ('White ink on a BLACK slide', 'black'), ('White ink on a BLACK slide', 'black'),
                    ('White ink on a BLACK slide', 'black'), ('White ink on a BLACK slide', 'black')]
        for i, (caption, theme) in enumerate(captions):
            im = Image.new('RGB', (640, 480), 'black')
            canvas = Image.new('RGB', (640, 360), theme)
            draw = ImageDraw.Draw(canvas)
            color = 'black' if theme == 'white' else 'white'
            draw.rectangle((30, 30, 610, 65), fill=color)
            draw.text((90, 135), caption, fill=color, stroke_width=1)
            if 'extra' in caption:
                draw.text((90, 220), 'Added bullet point', fill=color)
            im.paste(canvas, (0, 60))
            im.save(movie_frames / ('f_%03d.png' % i))
        cls.video = cls.folder / 'test-movie.mp4'
        r = subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y',
                            '-framerate', '1', '-i', str(movie_frames / 'f_%03d.png'),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.video)],
                           capture_output=True)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors='replace'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_local_pdf_and_chapters(self):
        import pymupdf
        chapters = self.folder / 'chapters.json'
        chapters.write_text(json.dumps([{'start': 0, 'title': 'Opening'},
                                        {'start': 6, 'title': 'Second'},
                                        {'start': 10, 'title': 'Third'}]), encoding='utf-8')
        pdf = self.folder / 'result.pdf'
        self.assertEqual(v.main([str(self.video), '--output', str(pdf),
                                 '--chapters', str(chapters), '--min-free-gb', '0.01']), 0)
        result = json.loads(pdf.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(result['pages'], 3)
        self.assertLessEqual(result['pages'], 5)
        self.assertGreater(result['crop_xyxy'][1], 0)  # black bars auto-detected
        self.assertEqual(len(result['chapters']), 3)
        with pymupdf.open(pdf) as doc:
            self.assertEqual(len(doc), result['pages'])
        self.assertEqual(v.main([str(self.video), '--output', str(pdf),
                                 '--min-free-gb', '0.01']), 1)  # no overwrite by default

    def test_direct_url(self):
        # Local HTTP server emulates a direct authorized video URL. No network account.
        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
            *args, directory=str(self.folder), **kwargs)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = 'http://127.0.0.1:%d/%s' % (server.server_port, self.video.name)
            pdf = self.folder / 'from-url.pdf'
            self.assertEqual(v.main([url, '--output', str(pdf), '--min-free-gb', '0.01']), 0)
            self.assertTrue(pdf.stat().st_size > 500)
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=4)


if __name__ == '__main__':
    unittest.main()
