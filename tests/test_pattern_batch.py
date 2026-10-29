# -*- coding: utf-8 -*-
"""Batch download from an actually-observed media URL plus explicit session IDs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from media_sources import collect_from_har
from batch_by_pattern import (build_candidates, find_template, main, read_ids)
from test_authenticated_video import VideoServer, write_har


class PatternBatchTests(unittest.TestCase):
    def _fixture(self, root, url, ids=('100', '101')):
        har = Path(root) / 'private.har'
        write_har(har, url)
        ids_file = Path(root) / 'ids.txt'
        ids_file.write_text('\n'.join(ids), encoding='utf-8')
        return har, ids_file

    def test_url_changes_only_whole_session_id_segment(self):
        with tempfile.TemporaryDirectory() as td:
            har, _ = self._fixture(td, 'https://cdn.example.org/EADV/session/100/video.mp4?jwt=SENSITIVE')
            cand, part_idx, orig = find_template(collect_from_har(har).list())
            res = build_candidates(cand, part_idx, orig, ['100', '101'])
            self.assertEqual(res[1].url, 'https://cdn.example.org/EADV/session/101/video.mp4?jwt=SENSITIVE')
            self.assertEqual(res[1].headers['cookie'], 'sid=authorized')
            self.assertEqual(res[1].host, cand.host)

    def test_requires_observed_media_url(self):
        with tempfile.TemporaryDirectory() as td:
            har, _ = self._fixture(td, 'https://cdn.example.org/EADV/session/100/video.mp4')
            other = Path(td) / 'empty.har'
            other.write_text('{"log":{"entries":[]}}')
            with self.assertRaises(ValueError):
                find_template(collect_from_har(other).list())

    def test_ambiguous_video_requires_sample_id(self):
        with tempfile.TemporaryDirectory() as td:
            har, _ = self._fixture(td, 'https://cdn.example.org/session/100/video.mp4')
            data = json.loads(har.read_text())
            data['log']['entries'].append({'request': {'url': 'https://cdn.example.org/session/200/video.mp4',
                                                        'method':'GET', 'headers': []},
                                           'response': {'content': {'mimeType':'video/mp4'}}})
            har.write_text(json.dumps(data))
            cands = collect_from_har(har).list()
            with self.assertRaises(ValueError):
                find_template(cands)
            self.assertEqual(find_template(cands, '100')[2], '100')

    def test_unverified_extension_is_not_converted(self):
        with tempfile.TemporaryDirectory() as td:
            har, _ = self._fixture(td, 'https://cdn.example.org/session/100/player.html')
            data=json.loads(har.read_text())
            for item in data['log']['entries']:
                item['response']['content']['mimeType']='text/html'
            har.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                find_template(collect_from_har(har).list())

    def test_id_list_validation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'ids.txt'
            p.write_text('100\n101\n', encoding='utf-8')
            self.assertEqual(read_ids(p), ['100', '101'])
            p.write_text('100\n100\n', encoding='utf-8')
            with self.assertRaises(ValueError): read_ids(p)
            p.write_text('100\n../../private\n', encoding='utf-8')
            with self.assertRaises(ValueError): read_ids(p)
            csvfile = Path(td) / 'ids.csv'
            csvfile.write_text('id,title\n100,Example One\n101,Example Two\n',encoding='utf-8')
            self.assertEqual(read_ids(csvfile), ['100', '101'])

    def test_preview_never_downloads_and_never_outputs_credentials(self):
        with VideoServer() as web, tempfile.TemporaryDirectory() as td:
            base = web.url.replace('/video.mp4', '/session/100/video.mp4')
            har, ids = self._fixture(td, base)
            code = main(['--har',str(har),'--ids-file',str(ids),
                         '--allow-host','127.0.0.1','--dry-run','--out',str(Path(td)/'out')])
            self.assertEqual(code,0)
            self.assertEqual(len(web.received),0)

    def test_authenticated_batch_download_and_safe_report(self):
        with VideoServer() as web, tempfile.TemporaryDirectory() as td:
            base = web.url.replace('/video.mp4', '/session/100/video.mp4')
            har, ids = self._fixture(td, base)
            out = Path(td) / 'out'
            args = ['--har',str(har),'--ids-file',str(ids), '--allow-host','127.0.0.1',
                    '--allow-private-hosts','--download-only','--out',str(out),'--delay','0']
            self.assertEqual(main(args),0)
            self.assertEqual(len(list((out/'videos').glob('*.mp4'))),2)
            self.assertTrue(any('/session/101/video.mp4' in x['path'] for x in web.received))
            report=(out/'media-report.json').read_text(encoding='utf-8')
            self.assertNotIn('super-secret', report)
            self.assertNotIn('sid=authorized', report)
            self.assertNotIn('session/101/video.mp4', report)
            previous = len(web.received)
            self.assertEqual(main(args),0)
            self.assertEqual(len(web.received),previous)

    def test_stops_when_login_expires(self):
        with VideoServer() as web, tempfile.TemporaryDirectory() as td:
            base = web.url.replace('/video.mp4', '/session/100/video.mp4')
            har, ids = self._fixture(td, base)
            data=json.loads(har.read_text())
            for item in data['log']['entries']:
                item['request']['headers'] = []
            har.write_text(json.dumps(data))
            out=Path(td)/'out'
            status=main(['--har',str(har),'--ids-file',str(ids), '--allow-host','127.0.0.1',
                         '--allow-private-hosts','--download-only','--out',str(out),'--delay','0'])
            self.assertEqual(status,1)
            self.assertEqual(len(web.received),1)
            report=(out/'media-report.json').read_text()
            self.assertIn('authentication_required',report)
            self.assertNotIn('super-secret',report)


    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg needed for real PDF integration')
    def test_pattern_batch_download_to_two_pdfs(self):
        from PIL import Image, ImageDraw
        import pymupdf
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            frames = root/'frames'
            frames.mkdir()
            for i in range(9):
                frame = Image.new('RGB',(320,180),[(200,120,50),(40,180,200),(170,80,130)][i//3])
                draw=ImageDraw.Draw(frame)
                draw.text((25,70),f'PAGE {i//3}',fill='black')
                frame.save(frames/f'f{i:03d}.png')
            video=root/'fixture.mp4'
            p=subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-y','-framerate','1',
                              '-i',str(frames/'f%03d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(video)],
                             capture_output=True)
            self.assertEqual(p.returncode,0)
            with VideoServer(video.read_bytes()) as web:
                base=web.url.replace('/video.mp4','/session/100/video.mp4')
                har,ids=self._fixture(td,base)
                out=root/'out'
                code=main(['--har',str(har),'--ids-file',str(ids),
                           '--allow-host','127.0.0.1','--allow-private-hosts',
                           '--out',str(out),'--delay','0','--min-free-gb','0.01'])
                self.assertEqual(code,0)
                pdfs=list((out/'pdf').glob('*.pdf'))
                self.assertEqual(len(pdfs),2)
                for file in pdfs:
                    with pymupdf.open(file) as doc:
                        self.assertGreaterEqual(len(doc),2)
                self.assertNotIn('super-secret',(out/'media-report.json').read_text())


if __name__=='__main__': unittest.main()
