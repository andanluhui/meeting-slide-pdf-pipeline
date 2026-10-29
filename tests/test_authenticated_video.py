# -*- coding: utf-8 -*-
"""Stage three: authenticated local HTTP + HAR integration regressions."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from media_sources import (AuthenticationExpired, CaptureBuffer, MediaDownloadError,
                           collect_from_har, download_media, host_allowed,
                           make_candidate, public_report)
import authenticated_video as adapter


class VideoServer:
    def __init__(self, video=b'0000ftypisom'+b'x'*5000, redirect=None, set_cookie=False):
        self.video=video
        self.redirect=redirect
        self.set_cookie=set_cookie
        self.received=[]
        obj=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                obj.received.append({'path':self.path,'cookie':self.headers.get('Cookie'),
                                     'authorization':self.headers.get('Authorization'),
                                     'range':self.headers.get('Range')})
                if obj.redirect:
                    self.send_response(302)
                    self.send_header('Location',obj.redirect)
                    if obj.set_cookie:
                        self.send_header('Set-Cookie','sid=authorized; Path=/')
                    self.end_headers()
                    return
                if self.headers.get('Cookie') != 'sid=authorized' and self.headers.get('Authorization') != 'Bearer authorized':
                    self.send_response(403)
                    self.end_headers()
                    return
                start=0
                range_text=self.headers.get('Range') or ''
                if range_text.startswith('bytes='):
                    start=int(range_text[6:-1])
                if start:
                    self.send_response(206)
                    self.send_header('Content-Range',f'bytes {start}-{len(obj.video)-1}/{len(obj.video)}')
                else:
                    self.send_response(200)
                self.send_header('Content-Type','video/mp4')
                self.send_header('Content-Length',str(len(obj.video)-start))
                self.end_headers()
                self.wfile.write(obj.video[start:])
            def log_message(self,*args):
                return
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/video.mp4?signature=super-secret'
    def close(self):
        self.server.shutdown();self.thread.join(timeout=3);self.server.server_close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def write_har(path,url,auth=True,extra=False):
    headers=[{'name':'User-Agent','value':'FakeBrowser/1.0'}]
    if auth:
        headers.append({'name':'Cookie','value':'sid=authorized'})
    entry={'request':{'url':url,'method':'GET','headers':headers},
           'response':{'status':200,'content':{'mimeType':'video/mp4'}}}
    entries=[entry,dict(entry)]
    if extra:
        entries.extend([{'request':{'url':'https://media.example.org/show.m3u8','headers':[]},
                         'response':{'content':{'mimeType':'application/vnd.apple.mpegurl'}}},
                        {'request':{'url':'https://media.example.org/ad.png','headers':[]},
                         'response':{'content':{'mimeType':'image/png'}}}])
    path.write_text(json.dumps({'log':{'entries':entries}}),encoding='utf-8')


class AuthorizedSourceTests(unittest.TestCase):
    def test_har_deduplicates_and_redacts(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'private.har'
            write_har(path,'https://cdn.example.org/video.mp4?token=top-secret',extra=True)
            buffer=collect_from_har(path)
            self.assertEqual(len(buffer.list()),2)
            report=json.dumps(public_report(buffer.list(),[]))
            self.assertNotIn('top-secret',report)
            self.assertNotIn('sid=authorized',report)
            self.assertNotIn('video.mp4?',report)
            self.assertEqual(buffer.hosts(),{'cdn.example.org':1,'media.example.org':1})

    def test_host_allowlist_is_exact(self):
        self.assertTrue(host_allowed('cdn.example.org',['cdn.example.org']))
        self.assertTrue(host_allowed('a.cdn.example.org',['*.cdn.example.org']))
        self.assertFalse(host_allowed('evilcdn.example.org',['cdn.example.org']))
        self.assertFalse(host_allowed('cdn.example.org.attacker.net',['cdn.example.org']))

    def test_login_required(self):
        with VideoServer() as web, tempfile.TemporaryDirectory() as td:
            item=make_candidate(web.url,'video/mp4')
            with self.assertRaises(AuthenticationExpired):
                download_media(item,Path(td)/'result.mp4',['127.0.0.1'],allow_private=True)
            self.assertFalse((Path(td)/'result.mp4').exists())

    def test_authenticated_download_and_skip(self):
        with VideoServer() as web, tempfile.TemporaryDirectory() as td:
            item=make_candidate(web.url,'video/mp4',{'cookie':'sid=authorized'})
            file=Path(td)/'result.mp4'
            self.assertEqual(download_media(item,file,['127.0.0.1'],allow_private=True)['status'],'downloaded')
            self.assertEqual(file.read_bytes(),web.video)
            count=len(web.received)
            self.assertEqual(download_media(item,file,['127.0.0.1'],allow_private=True)['status'],'skipped')
            self.assertEqual(len(web.received),count)

    def test_resume_partial_download(self):
        with VideoServer() as web, tempfile.TemporaryDirectory() as td:
            item=make_candidate(web.url,'video/mp4',{'cookie':'sid=authorized'})
            file=Path(td)/'result.mp4'
            file.with_name(file.name+'.part').write_bytes(web.video[:100])
            self.assertEqual(download_media(item,file,['127.0.0.1'],allow_private=True)['status'],'downloaded')
            self.assertEqual(file.read_bytes(),web.video)
            self.assertEqual(web.received[0]['range'],'bytes=100-')

    def test_cookie_not_forwarded_on_cross_port_redirect(self):
        with VideoServer() as dest:
            with VideoServer(redirect=dest.url, set_cookie=True) as origin, tempfile.TemporaryDirectory() as td:
                item=make_candidate(origin.url,'video/mp4',{'cookie':'sid=authorized',
                                                             'authorization':'Bearer authorized'})
                with self.assertRaises(AuthenticationExpired):
                    download_media(item,Path(td)/'result.mp4',['127.0.0.1'],allow_private=True)
                self.assertIsNone(dest.received[0]['cookie'])
                self.assertIsNone(dest.received[0]['authorization'])

    def test_private_hosts_need_explicit_permission(self):
        with VideoServer() as web, tempfile.TemporaryDirectory() as td:
            item=make_candidate(web.url,'video/mp4',{'cookie':'sid=authorized'})
            with self.assertRaises(MediaDownloadError):
                download_media(item,Path(td)/'result.mp4',['127.0.0.1'])
            self.assertFalse(web.received)

    def test_playlist_report_without_download(self):
        buffer=CaptureBuffer()
        buffer.add('https://media.example.org/video/index.m3u8','application/vnd.apple.mpegurl')
        self.assertEqual(buffer.list()[0].kind,'playlist')

    @unittest.skipUnless(shutil.which('chromium'), 'System Chromium required for mocked browser capture')
    def test_browser_observes_mocked_media_requests(self):
        """Playwright response listener works without any real login or external website."""
        from playwright.sync_api import sync_playwright
        import hashlib
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True,
                         executable_path=shutil.which('chromium'), args=['--no-sandbox'])
            try:
                context=browser.new_context()
                page=context.new_page()
                buffer=CaptureBuffer()
                def observer(res):
                    buffer.add(res.url, res.headers.get('content-type',''),
                               res.request.all_headers())
                context.on('response',observer)
                page.route('https://media.example.org/**', lambda route: route.fulfill(
                    status=200, content_type='video/mp4', body=b'fake-mp4'))
                page.goto('about:blank')
                page.evaluate("() => {let v=document.createElement(\'video\');v.src=\'https://media.example.org/video.mp4?token=SECRET\';v.muted=true;v.autoplay=true;document.body.appendChild(v)}")
                page.wait_for_timeout(700)
                self.assertEqual(len(buffer.list()),1)
                self.assertEqual(buffer.list()[0].host,'media.example.org')
                self.assertNotIn('SECRET',json.dumps(public_report(buffer.list(),[])))
            finally:
                browser.close()

    def test_invalid_har_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.har';p.write_text(json.dumps({'log':{'entries':{}}}))
            with self.assertRaises(ValueError):
                collect_from_har(p)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for end-to-end test')
    def test_har_download_to_pdf_and_no_secret_report(self):
        from PIL import Image,ImageDraw
        import pymupdf
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);frames=root/'frames';frames.mkdir()
            for i in range(9):
                image=Image.new('RGB',(320,180),((200,180,50),(50,190,200),(190,50,100))[i//3])
                draw=ImageDraw.Draw(image)
                draw.rectangle((20,20,270,60),fill='black')
                draw.text((30,90),f'SLIDE {i//3}',fill='black')
                image.save(frames/('f%03d.png'%i))
            video=root/'source.mp4'
            proc=subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-y',
                                 '-framerate','1','-i',str(frames/'f%03d.png'),
                                 '-c:v','libx264','-pix_fmt','yuv420p',str(video)],capture_output=True)
            self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'))
            with VideoServer(video.read_bytes()) as web:
                har=root/'secret.har';write_har(har,web.url)
                out=root/'results'
                code=adapter.main(['har','--har',str(har),'--allow-host','127.0.0.1',
                                   '--allow-private-hosts','--out',str(out),
                                   '--min-free-gb','0.01'])
                self.assertEqual(code,0)
                pdfs=list((out/'pdf').glob('*.pdf'))
                self.assertEqual(len(pdfs),1)
                with pymupdf.open(pdfs[0]) as doc:
                    self.assertGreaterEqual(len(doc),3)
                text=(out/'media-report.json').read_text()
                self.assertNotIn('super-secret',text)
                self.assertNotIn('sid=authorized',text)
                self.assertEqual(adapter.main(['har','--har',str(har),'--allow-host','127.0.0.1',
                                               '--allow-private-hosts','--out',str(out),
                                               '--min-free-gb','0.01']),0)
                self.assertEqual(len(list((out/'videos').glob('*.mp4'))),1)


if __name__=='__main__':
    unittest.main()
