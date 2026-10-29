# -*- coding: utf-8 -*-
"""Evidence-based review flags for candidate slide extraction.

Flags are not assertions that a page is missing or that content is incorrect.
They prioritize manual QA and keep all candidate pages until reviewed.
"""
from __future__ import annotations

from pathlib import Path


def audit(pages, frames, crop, layout, base_fps, scene_count, *, duration_hint=None,
          contacts_per_sheet=120):
    from PIL import Image
    import numpy as np

    alerts = []
    for note in layout.get('notes', []):
        if layout.get('strategy') != 'presenter_strip':
            alerts.append({'code': 'layout_review', 'severity': 'review', 'message': note})
    if len(frames) < 4:
        alerts.append({'code': 'low_sampling', 'severity': 'review',
                       'message': 'Very few decoded frames; short slides could be skipped.'})
    if scene_count == 0:
        alerts.append({'code': 'no_scene_boost_hits', 'severity': 'info',
                       'message': 'Scene-boost found no extra transitions; fine changes rely on fixed-rate samples.'})
    if len(pages) > contacts_per_sheet:
        alerts.append({'code': 'many_pages', 'severity': 'info',
                       'message': 'Contact preview is split into multiple images; review every image.'})
    if pages and pages[0]['start_sec'] > 4:
        alerts.append({'code': 'late_first_page', 'severity': 'review',
                       'message': 'First candidate slide appears after the start; check any opening material.'})

    quality_pages = []
    for i, p in enumerate(pages):
        start = p['start_sec']
        stop = pages[i+1]['start_sec'] if i+1 < len(pages) else duration_hint
        duration = max(0, round(stop-start, 3)) if stop is not None else None
        flags = []
        if duration is not None and duration < 1.25:
            flags.append('brief_page: verify this is a real slide, not a transition frame')
        if duration is not None and duration > 300:
            flags.append('long_gap: inspect for missed pages during this interval')
        with Image.open(p['frame']) as im:
            im = im.convert('L').crop(crop).resize((160, 90))
            a = np.asarray(im, dtype=np.float32)
        std = float(a.std())
        if std < 13:
            flags.append('low_detail: possible blank, title, or transition frame')
        quality_pages.append({'number': i+1, 'start_sec': start,
                              'end_sec': round(stop, 3) if stop is not None else None,
                              'approx_duration_sec': duration,
                              'sample_count': p.get('sample_count', 1),
                              'review_flags': flags})
    n_brief = sum(any(x.startswith('brief_page') for x in p['review_flags']) for p in quality_pages)
    n_long = sum(any(x.startswith('long_gap') for x in p['review_flags']) for p in quality_pages)
    n_low = sum(any(x.startswith('low_detail') for x in p['review_flags']) for p in quality_pages)
    if n_brief:
        alerts.append({'code': 'brief_pages', 'severity': 'review',
                       'count': n_brief, 'message': 'Short-lived page candidates require visual confirmation.'})
    if n_long:
        alerts.append({'code': 'long_gaps', 'severity': 'review',
                       'count': n_long, 'message': 'Long gaps might contain unnoticed slide changes.'})
    if n_low:
        alerts.append({'code': 'low_detail_pages', 'severity': 'review',
                       'count': n_low, 'message': 'Low-detail pages could be transitions or minimal title slides.'})
    if len(pages) > len(frames)*0.45 and len(frames) >= 10:
        alerts.append({'code': 'high_change_rate', 'severity': 'review',
                       'message': 'Unusually many slide candidates relative to sampled frames; possible motion false positives.'})
    return {'disclaimer': 'Heuristic review flags, not proof of completeness. Inspect source video if needed.',
            'alerts': alerts, 'review_count': sum(bool(x['review_flags']) for x in quality_pages),
            'pages': quality_pages, 'suggested_actions': [
                'Inspect all contact sheets and especially flagged pages.',
                'If speaker/sidebar is retained, rerun with --crop to isolate slides.',
                'If brief slides remain missing, increase --fps (maximum 5) and rerun.',
            ]}


def suggest_chapters(pages, crop, *, minimum_spacing_sec=120.0):
    """Suggest *provisional* section markers from sparse, title-like visual resets.

    This does not infer actual speaker names or semantic topic boundaries and
    must not silently replace authoritative chapter markers or agenda entries.
    """
    from PIL import Image
    import numpy as np
    proposals = [{'start': 0.0, 'title': 'Section 01',
                  'confidence': 'anchor', 'reason': 'Video start'}]
    prev = None
    previous_boundary = 0.0
    for p in pages:
        t = float(p['start_sec'])
        with Image.open(p['frame']) as src:
            a = np.asarray(src.convert('RGB').crop(crop).resize((160,90)), dtype=np.float32)
        if prev is not None and t - previous_boundary >= minimum_spacing_sec:
            color_jump = float(np.mean(np.abs(a - prev)))
            gray = a.mean(axis=2)
            # A sparse/title-like slide often separates lecture chapters.
            neutral = (float(gray.std()) < 43 or
                       float(((gray < 55) | (gray > 205)).mean()) > .86)
            if color_jump > 45 and neutral:
                proposals.append({'start': round(t,3),
                                  'title': 'Section %02d' % (len(proposals)+1),
                                  'confidence': 'low',
                                  'reason': 'Large visual reset and sparse/title-like frame; verify manually'})
                previous_boundary = t
        prev = a
    return proposals
