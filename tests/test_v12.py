# -*- coding: utf-8 -*-
"""Stage two regressions: conservative layout, short pages, and full QA."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import video_to_pdf as video
from layout_analysis import inspect_layout
from quality_review import audit


class StageTwoTests(unittest.TestCase):
    def test_auto_layout_finds_presenter_sidebar(self):
        import numpy as np
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            frames=[]
            for i in range(24):
                im=Image.new('RGB',(640,360),'#fafafa')
                draw=ImageDraw.Draw(im)
                draw.text((40,40),'Fixed lecture content '+str(i//12),fill='black')
                draw.rectangle((0,0,468,359),outline='#dedede',width=2)
                # Highly animated/independent webcam region with a hard divider.
                arr=np.asarray(im).copy()
                # Alternating webcam motion remains visible after downsampling.
                arr[:,470:,:]=(40,70,110) if i%2==0 else (190,150,125)
                im=Image.fromarray(arr)
                p=folder/('f%03d.png'%i);im.save(p)
                frames.append((i,p))
            crop,meta=inspect_layout(frames,(0,0,640,360),'auto')
            self.assertEqual(meta['strategy'],'presenter_strip',meta)
            self.assertLessEqual(abs(crop[2]-470),14)
            self.assertEqual(crop[0],0)
            self.assertTrue(meta['candidates'])
            preview=folder/'layout.jpg'
            from layout_analysis import save_layout_preview
            save_layout_preview(frames,crop,preview)
            self.assertGreater(preview.stat().st_size,2000)

    def test_auto_layout_falls_back_for_fullscreen_motion(self):
        import numpy as np
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            frames=[]
            for i in range(12):
                rng=np.random.default_rng(i+100)
                arr=rng.integers(10,220,(180,320,3),dtype=np.uint8)
                p=Path(td)/('%03d.png'%i);Image.fromarray(arr).save(p)
                frames.append((i,p))
            crop,meta=inspect_layout(frames,(0,0,320,180),'auto')
            self.assertEqual(crop,(0,0,320,180))
            self.assertEqual(meta['strategy'],'blackbars')
            self.assertTrue(meta['notes'])

    def test_provisional_auto_chapter_suggestions(self):
        from PIL import Image, ImageDraw
        from quality_review import suggest_chapters
        with tempfile.TemporaryDirectory() as td:
            p1=Path(td)/'first.png';p2=Path(td)/'title.png'
            # Rich slide followed >120s later by a sparse title reset.
            Image.new('RGB',(320,180),(0,100,180)).save(p1)
            a=Image.new('RGB',(320,180),'white')
            ImageDraw.Draw(a).text((50,40),'SECTION TITLE',fill='black')
            a.save(p2)
            pages=[{'start_sec':0,'frame':str(p1)},
                   {'start_sec':140,'frame':str(p2)}]
            result=suggest_chapters(pages,(0,0,320,180),minimum_spacing_sec=120)
            self.assertEqual(len(result),2,result)
            self.assertEqual(result[1]['confidence'],'low')
            short=suggest_chapters(pages,(0,0,320,180),minimum_spacing_sec=300)
            self.assertEqual(len(short),1)

    def test_quality_flags_long_gaps_and_brief_pages(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'p.png'; Image.new('RGB',(320,180),'white').save(p)
            pages=[{'frame':str(p),'start_sec':0.0},
                   {'frame':str(p),'start_sec':0.4},
                   {'frame':str(p),'start_sec':450.0}]
            frames=[(i,p) for i in range(5)]
            result=audit(pages,frames,(0,0,320,180),
                         {'strategy':'blackbars','notes':[]},2,1,duration_hint=455)
            codes=[a['code'] for a in result['alerts']]
            self.assertIn('brief_pages',codes)
            self.assertIn('long_gaps',codes)
            self.assertIn('low_detail_pages',codes)

    def test_contact_sheets_do_not_truncate(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            frame=Path(td)/'frame.png';Image.new('RGB',(100,60),'white').save(frame)
            pages=[{'frame':str(frame),'start_sec':float(i)} for i in range(123)]
            out=Path(td)/'contact.jpg'
            files=video.save_contact_sheets(pages,(0,0,100,60),out,overwrite=False)
            self.assertEqual(len(files),2)
            self.assertTrue(all(x.exists() for x in files))
            with self.assertRaises(FileExistsError):
                video.save_contact_sheets(pages,(0,0,100,60),out,overwrite=False)


    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_full_pipeline_with_presenter_strip(self):
        from PIL import Image, ImageDraw
        import pymupdf
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td); frames=folder/'frames'; frames.mkdir()
            for i in range(15):
                im=Image.new('RGB',(640,360),'white')
                d=ImageDraw.Draw(im)
                d.rectangle((2,2,469,357),outline=(220,220,220),width=3)
                d.rectangle((25,30,420,65),fill=(5,5,5))
                d.rectangle((55,100,400,275),fill=((180,220,80),(220,120,70),(100,170,225))[i//5])
                d.text((30,125),'SLIDE '+str(i//5),fill=(5,5,5))
                # Webcam sidebar changes independently every second.
                d.rectangle((470,0,639,359),fill=((40,80,120) if i%2==0 else (160,120,80)))
                im.save(frames/('f%03d.png'%i))
            video_path=folder/'split.mp4'; out=folder/'slides.pdf'
            proc=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
                                 '-framerate','1','-i',str(frames/'f%03d.png'),
                                 '-c:v','libx264','-pix_fmt','yuv420p',str(video_path)],capture_output=True)
            self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'))
            self.assertEqual(video.main([str(video_path),'--output',str(out),
                                         '--auto-chapters','--min-free-gb','0.01']),0)
            result=json.loads(out.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(result['version'],'1.2')
            self.assertEqual(result['layout']['strategy'],'presenter_strip',result['layout'])
            self.assertTrue(440<result['crop_xyxy'][2]<500)
            self.assertTrue(Path(result['layout_preview']).exists())
            self.assertTrue(isinstance(result['quality']['alerts'],list))
            self.assertEqual(len(result['contact_sheets']),1)
            self.assertEqual(len(result['chapters']),1)
            self.assertEqual(result['chapter_suggestions'][0]['confidence'],'anchor')
            with pymupdf.open(out) as pdf:
                self.assertGreaterEqual(len(pdf),3)
                self.assertLessEqual(len(pdf),6)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_scene_boost_catches_subsecond_interstitial(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as td:
            tmp=Path(td)
            images=tmp/'input';images.mkdir()
            # 5 seconds at 20 fps; middle slide lasts 0.20 seconds and
            # occurs between integer and half-integer sampling instants.
            for i in range(100):
                sec=i/20
                if sec < 2.30:
                    bg=(245,245,245);txt='BEGIN'
                elif sec < 2.45:
                    bg=(5,25,115);txt='BRIEF SPECIAL SLIDE'
                else:
                    bg=(215,225,30);txt='FOLLOWING'
                im=Image.new('RGB',(320,180),bg)
                d=ImageDraw.Draw(im);d.rectangle((20,30,300,60),fill='black');d.text((50,85),txt,fill='black')
                im.save(images/('f%04d.png'%i))
            movie=tmp/'movie.mp4'
            proc=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
                                 '-framerate','20','-i',str(images/'f%04d.png'),
                                 '-c:v','libx264','-pix_fmt','yuv420p',str(movie)],capture_output=True)
            self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'))
            work=tmp/'work';work.mkdir()
            fixed=video.extract_frames(movie,work,fps=2,max_frames=300)
            base=video.choose_pages(fixed,(0,0,320,180),.025)
            def has_brief(pages):
                return any(Image.open(p['frame']).convert('RGB').getpixel((160,160))[2] >
                           Image.open(p['frame']).convert('RGB').getpixel((160,160))[0]+45
                           for p in pages)
            self.assertFalse(has_brief(base),base)
            bonus,summary=video.extract_scene_frames(movie,work,scene_threshold=.12,max_extra=60)
            self.assertEqual(summary['status'],'ok',summary)
            self.assertGreaterEqual(len(bonus),2,bonus)
            mixed=video.merge_samples(fixed,bonus,300)
            improved=video.choose_pages(mixed,(0,0,320,180),.025)
            self.assertTrue(has_brief(improved),improved)
            flagged=audit(improved,mixed,(0,0,320,180),
                          {'strategy':'blackbars','notes':[]},2,len(bonus),duration_hint=5)
            self.assertGreaterEqual(flagged['review_count'],1)
            self.assertTrue(any(a['code']=='brief_pages' for a in flagged['alerts']))


if __name__=='__main__':unittest.main()
