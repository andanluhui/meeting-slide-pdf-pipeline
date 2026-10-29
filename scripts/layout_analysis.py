# -*- coding: utf-8 -*-
"""Conservative, multi-frame detection of a dominant static slide region.

Never crop on a guess. A consistent divider plus a moving presenter sideband
is required; otherwise retain the full (possibly black-bar-trimmed) picture.
"""
from __future__ import annotations


def _evenly_spaced_pairs(frames, max_pairs=28):
    count = len(frames) - 1
    if count < 1:
        return []
    if count <= max_pairs:
        ids = list(range(count))
    else:
        ids = sorted(set(round(i * (count - 1) / (max_pairs - 1)) for i in range(max_pairs)))
    return [(frames[i][1], frames[i + 1][1]) for i in ids]


def inspect_layout(frames, base_crop, mode='auto'):
    """Return (crop xyxy, evidence) with a fallback for ambiguous layouts.

    Only strong vertical or horizontal dividers with a much-more-active
    smaller side can produce auto crop. Coordinates use original frame pixels.
    """
    import numpy as np
    from PIL import Image

    if mode not in ('auto', 'full', 'blackbars'):
        raise ValueError('--layout must be auto, blackbars or full')
    x0, y0, x1, y1 = base_crop
    info = {'mode': mode, 'strategy': 'full' if mode == 'full' else 'blackbars',
            'confidence': 'not_applicable' if mode != 'auto' else 'low',
            'crop_xyxy': list(base_crop), 'candidates': [], 'notes': []}
    if mode != 'auto' or len(frames) < 5:
        if mode == 'auto':
            info['notes'].append('Too few frames to identify a stable slide/presenter split.')
        return base_crop, info

    pairs = _evenly_spaced_pairs(frames, 28)
    sample_files = sorted(set(str(p) for pair in pairs for p in pair))
    arrays = {}
    size = (240, 135)
    for fname in sample_files:
        with Image.open(fname) as image:
            arrays[fname] = np.asarray(image.convert('RGB').crop(base_crop).resize(size), dtype=np.int16)
    # Motion is measured between nearby times, not arbitrary frames from
    # different slides. Median suppresses infrequent full-slide transitions.
    changes = [np.mean(np.abs(arrays[str(b)] - arrays[str(a)]), axis=2) > 23
               for a, b in pairs]
    if not changes:
        return base_crop, info
    activity = np.mean(np.stack(changes, axis=0).astype(np.float32), axis=0)
    mean_image = np.median(np.stack([arrays[f] for f in sample_files[::max(1, len(sample_files)//12)]], axis=0), axis=0)
    gray = (0.299*mean_image[:, :, 0] + 0.587*mean_image[:, :, 1] +
            0.114*mean_image[:, :, 2])
    candidates = []

    # Scan only plausible split positions; never auto-crop away more than 45%.
    for axis in ('vertical', 'horizontal'):
        profile = activity.mean(axis=0 if axis == 'vertical' else 1)
        length = len(profile)
        for split in range(round(length*0.16), round(length*0.84)):
            if split < 3 or length-split < 3:
                continue
            for keep in ('before', 'after'):
                kept_width = split if keep == 'before' else length-split
                if kept_width < length*0.54:
                    continue
                kept = profile[:split] if keep == 'before' else profile[split:]
                side = profile[split:] if keep == 'before' else profile[:split]
                kept_motion = float(kept.mean())
                side_motion = float(side.mean())
                # Side must have persistent motion, while slide remains mostly static.
                if side_motion < 0.075 or side_motion < kept_motion*3.5 + 0.055:
                    continue
                if kept_motion > 0.09:
                    continue
                if axis == 'vertical':
                    edge_values = np.abs(gray[:, split-2] - gray[:, split+1])
                else:
                    edge_values = np.abs(gray[split-2, :] - gray[split+1, :])
                border_strength = float((edge_values > 35).mean())
                # Reject dividing a moving presenter, bullet transition or watermark.
                if border_strength < 0.40:
                    continue
                contrast = side_motion - kept_motion
                score = contrast * (0.6+border_strength) * (kept_width/length)
                candidates.append({'axis': axis, 'keep': keep,
                                   'fraction': round(split/length, 3),
                                   'slide_motion': round(kept_motion, 3),
                                   'side_motion': round(side_motion, 3),
                                   'border_strength': round(border_strength, 3),
                                   'score': round(score, 4), 'split': split})
    candidates.sort(key=lambda c: c['score'], reverse=True)
    info['candidates'] = [{k:v for k,v in c.items() if k!='split'} for c in candidates[:3]]
    if not candidates:
        info['notes'].append('No reliable slide/presenter divider; kept entire image. Use --crop if needed.')
        return base_crop, info
    best = candidates[0]
    # Strong evidence required. Moderate split stays a suggestion, not a crop.
    if best['border_strength'] < 0.55 or best['side_motion'] - best['slide_motion'] < 0.13:
        info['notes'].append('Possible layout split needs review; use --crop to choose explicitly.')
        return base_crop, info
    fraction = best['fraction']
    if best['axis'] == 'vertical':
        boundary = x0 + round((x1-x0)*fraction)
        crop = (x0, y0, boundary, y1) if best['keep']=='before' else (boundary, y0, x1, y1)
    else:
        boundary = y0 + round((y1-y0)*fraction)
        crop = (x0, y0, x1, boundary) if best['keep']=='before' else (x0, boundary, x1, y1)
    info['strategy'] = 'presenter_strip'
    info['confidence'] = 'high'
    info['crop_xyxy'] = list(crop)
    info['notes'].append('Detected a strong presenter/slide split; visually confirm the outlined crop.')
    return crop, info


def save_layout_preview(frames, crop, output: Path, overwrite=False):
    """Generate diagnostic thumbnails with selected crop outlined. No external upload."""
    from PIL import Image, ImageDraw
    if output.exists() and not overwrite:
        raise FileExistsError('Layout preview already exists: %s' % output)
    ids = sorted(set(round(i*(len(frames)-1)/5) for i in range(6)))
    w, h = 360, 230
    canvas = Image.new('RGB', (3*w, 2*h), 'white')
    draw = ImageDraw.Draw(canvas)
    for i, index in enumerate(ids):
        t, frame = frames[index]
        with Image.open(frame) as src:
            im = src.convert('RGB')
        scale_x, scale_y = (w-8)/im.width, (h-30)/im.height
        thumb = im.resize((round(im.width*scale_x),round(im.height*scale_y)))
        left, top = (i%3)*w+4, (i//3)*h+4
        canvas.paste(thumb,(left,top))
        c = (left+round(crop[0]*scale_x), top+round(crop[1]*scale_y),
             left+round(crop[2]*scale_x), top+round(crop[3]*scale_y))
        draw.rectangle(c, outline=(240,50,50), width=3)
        draw.text((left+6,top+h-24),'%.2fs' % t, fill='black')
    canvas.save(output, quality=90)
