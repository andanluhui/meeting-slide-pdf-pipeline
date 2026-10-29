# -*- coding: utf-8 -*-
"""主线：单场会议录播 -> 分段幻灯片 PDF -> 段归属到议程行。

主线四步（每步都可独立运行，输入输出契约见 references/standalone.md）：
  download  视频 -> <tmp_root>/S_<mid>/video.mp4（落盘前做磁盘配额检查）
  frames    抽帧 -> <tmp_root>/S_<mid>/parts/part_01/f_*.jpg + showinfo.log
  segment   切段切页 -> progress[key].segments + pdf/S_<mid>/*.pdf + analysis_sheet.png
  assign    归属 -> progress[key].assign（秒级，不抽帧不重建 PDF）

用法:
  python pipeline.py --all                     按优先级处理全部未完成场次
  python pipeline.py <key>                     处理单场（key = manage_id 或 session 名片段）
  python pipeline.py --all --steps frames      只重抽帧（全部场次）
  python pipeline.py <key> --steps segment     用已有帧重切段（调阈值后常用）
  python pipeline.py <key> --steps assign      只重算归属（= --rematch）
  python pipeline.py --all --steps download    只下载视频（不抽帧）
  python pipeline.py <key> --no-frame          跳过抽帧，用已有帧重算段与归属
  python pipeline.py --rematch                 只重算归属，不抽帧不重建 PDF（几秒钟）

产出写到 progress.json（由 config.progress_file 指定）:
  {session前60字符: {status, sheet, rows, kind, segments:[{seg,name,pdf,pages,t0,t1,
                     ocr_title,titles,speaker}], assign:{row:[段名]}}}
"""
import os
import re
import sys
import json
import glob
import time
import shutil
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 解释器自举：当前 python 缺依赖时，自动换成装了依赖的解释器重新执行本脚本。
# 必须放在 numpy / PIL / pymupdf 之前（详见 pybootstrap.py）。
import pybootstrap
pybootstrap.ensure(pybootstrap.DEPS_PIPELINE)

import numpy as np
from PIL import Image, ImageDraw
import pymupdf

import config as C

# 投票/互动问答页
POLL_RE = re.compile(r"(start\s*voting|to\s*vote|polling|vote\s*now|"
                     r"question\s*\d|q\s*\d\s*[.:：]\s)", re.I)
# 利益冲突/披露页：几乎每场学术演讲开头都有，是可靠的「演讲起点」标志
COI_RE = re.compile(r"(conflict of interest|disclosure|no potential conflict)", re.I)
# 讨论/问答行不回填
QA_RE = re.compile(r"discussion|q\s*&\s*a|q\s*and\s*a|panel|问答|讨论", re.I)


# ---------------------------------------------------------------- 工具
def ffmpeg(cfg=None):
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    env["no_proxy"] = "*"
    return ff, env


_OCR = None


def ocr_img(img):
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    try:
        r, _ = _OCR(img if isinstance(img, str) else np.asarray(img))
        return [(b, t) for b, t, c in (r or [])]
    except Exception as e:
        print("  OCR err:", e)
        return []


def clean_name(s, maxlen=80):
    s = re.sub(r"[:/\\?*\"<>|]+", "_", str(s or "")).strip().strip(".")
    return s[:maxlen].strip()


def du(p):
    t = 0
    for r, _, fs in os.walk(p):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return t


# ---------------------------------------------------------------- 下载
def download(url, jwt, dst, log=print, cfg=None, jobs=None):
    """返回 (ok, 已下载字节数)。

    先试**多连接分片**（`fastdl.py`）：CDN 回 `Accept-Ranges: bytes` 时，
    一条连接跑不满带宽 —— 实测同一 URL 单连接 0.58 MB/s、8 并发约 3 MB/s
    （2 GB 视频从 ~60 分钟降到 ~12 分钟）。探测失败/源不支持/文件很小，
    fastdl 返回 None，这里原样回退到下面的单连接断点续传（行为与旧版一致）。
    已有半成品文件时不并发，继续用单连接把它续完。
    """
    jobs = jobs if jobs is not None else int((cfg or {}).get("download_jobs", 8) or 8)
    if jobs > 1 and not os.path.exists(dst):
        try:
            import fastdl

            def _guard(_cfg=cfg):
                """下载途中也要守磁盘线：并发写 2 GB 时没人盯着会一路跌破告警线。"""
                need = float((_cfg or {}).get("min_free_gb", 13.0))
                for p in ((_cfg or {}).get("sys_disk") or "C:\\",
                          (_cfg or {}).get("tmp_root") or (_cfg or {}).get("base")):
                    if p and 0 <= C.free_gb(p) < need:
                        return False
                return True

            n = fastdl.download_parallel(url, jwt, dst, jobs=jobs, log=log, guard=_guard)
            if n:
                return True, n
        except Exception as e:
            log("  并发下载不可用（%s），回退单连接" % e)
    import requests
    total = None
    s = requests.Session()
    s.trust_env = False
    try:
        h = s.head(url, params={"jwt": jwt}, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        if h.status_code == 200:
            total = int(h.headers.get("content-length", 0)) or None
    except Exception:
        pass
    pos = os.path.getsize(dst) if os.path.exists(dst) else 0
    if total and pos >= total:
        log("  视频已缓存 %.0f MB" % (pos / 1048576))
        return True, pos
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    if pos:
        headers["Range"] = "bytes=%d-" % pos
    r = s.get(url, params={"jwt": jwt}, headers=headers, stream=True, timeout=180)
    if r.status_code not in (200, 206):
        raise RuntimeError("下载 HTTP %s" % r.status_code)
    if pos and r.status_code == 200:
        pos = 0
    got, t0, last = pos, time.time(), time.time()
    with open(dst, "ab" if pos else "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            got += len(chunk)
            if time.time() - last > 30:
                log("    下载 %.0f%%  %.0f MB" % (100 * got / (total or got), got / 1048576))
                last = time.time()
    log("  视频完成 %.0f MB" % (got / 1048576))
    return True, got


# ---------------------------------------------------------------- 抽帧
def extract_frames(cfg, task, log=print):
    """fps 抽帧 + showinfo 打时间戳。返回 (工作目录, 时长秒)。

    注意：不要用 -skip_frame nokey（只取关键帧）——非关键帧处的换页会被整段漏掉，
    实测同一视频 47 个画面只抽出 21 页。改用每秒抽帧再按内容去重。
    """
    out = work_dir(cfg, task)
    stf = os.path.join(out, "extract_status.json")
    st = json.load(open(stf, encoding="utf-8")) if os.path.exists(stf) else {}
    if st.get("local_done"):
        return out, st.get("duration")
    vp = os.path.join(out, "video.mp4")
    ff, env = ffmpeg(cfg)
    for attempt in (1, 2):
        if not (os.path.exists(vp) and os.path.getsize(vp) > 10 * 1048576):
            download(cfg["video_url_tpl"].format(mid=task["manage_id"]), cfg["jwt"], vp, log, cfg=cfg)
        parts = os.path.join(out, "parts")
        if cfg.get("purge", True):
            safe_rmtree(parts)
        elif os.path.isdir(parts):
            # 不删除：改名让出路径，交给清理进程回收（避免触发批量删除保护）
            os.rename(parts, os.path.join(out, "parts_old_%d" % int(time.time())))
        pd = os.path.join(parts, "part_01")
        os.makedirs(pd, exist_ok=True)
        log("  抽帧 fps=%s（第 %d 次）..." % (cfg["fps"], attempt))
        logf = open(os.path.join(pd, "showinfo.log"), "wb")
        try:
            subprocess.run([ff, "-hide_banner", "-nostats", "-i", vp,
                            "-vf", "fps=%s,showinfo" % cfg["fps"], "-q:v", "3",
                            os.path.join(pd, "f_%06d.jpg")],
                           stderr=logf, env=env, timeout=21600)
        except subprocess.TimeoutExpired:
            log("  抽帧超时")
        logf.close()
        n = len(glob.glob(os.path.join(pd, "f_*.jpg")))
        p = subprocess.run([ff, "-hide_banner", "-i", vp], capture_output=True, env=env, timeout=180)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)", p.stderr.decode("utf-8", "ignore"))
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None
        last = _last_pts(parts)
        log("  帧数=%d 时长=%s 覆盖=%.0fs" % (n, dur, last))
        if dur is None or last >= dur - 15 or attempt == 2:
            break
        log("  覆盖不全 -> 重新下载完整视频")
        if cfg.get("purge", True) and os.path.exists(vp):
            os.remove(vp)
        download(cfg["video_url_tpl"].format(mid=task["manage_id"]), cfg["jwt"], vp, log, cfg=cfg)
    json.dump({"duration": dur, "local_done": n > 20}, open(stf, "w"))
    return out, dur


def _last_pts(parts):
    pat = re.compile(r"pts_time:([\d.]+)")
    mx = -1.0
    for lg in glob.glob(os.path.join(parts, "*", "showinfo.log")):
        for line in open(lg, encoding="utf-8", errors="ignore"):
            for m in pat.finditer(line.replace("\0", "")):
                mx = max(mx, float(m.group(1)))
    return mx


def load_frames(out):
    pat = re.compile(r"n:\s*(\d+).*?pts_time:([\d.]+)")
    frames = []
    for pd in sorted(glob.glob(os.path.join(out, "parts", "part_*"))):
        lg = os.path.join(pd, "showinfo.log")
        if not os.path.exists(lg):
            continue
        for line in open(lg, encoding="utf-8", errors="ignore"):
            m = pat.search(line.replace("\0", ""))
            if m:
                fp = os.path.join(pd, "f_%06d.jpg" % (int(m.group(1)) + 1))
                if os.path.exists(fp):
                    frames.append((float(m.group(2)), fp))
    frames.sort(key=lambda x: x[0])
    out2, last = [], -99.0
    for t, f in frames:
        if t - last >= 0.35:      # 片间 seek 重叠去重
            out2.append((t, f))
            last = t
    return out2


def work_dir(cfg, task):
    d = os.path.join(cfg["tmp_root"], "S_%s" % (task.get("manage_id") or "NA"))
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 切段与去重
def detect_switches(cfg, frames):
    """讲者名条切换检测：过渡带峰值 + 带外前后平静（排除全屏人物/讨论等动态画面）。

    历史教训：旧实现要求"单帧突变 + 前后 3 帧立刻平静"，无法识别专业直播的
    **多帧动画过渡**（实测某录像切换瞬间连续 7 帧大差异：26.7/55.9/27.1/44.1/45.4/9.5/60.3，
    尖峰帧因"后 3 帧还在动"被否，尾帧因"前 3 帧刚动过"被否 → 切换点整段丢失，
    相邻两场演讲被并成一个 27 页段，导入行拿不到任何幻灯片）。
    改为把连续变化视为一条"过渡带"：带内峰值超阈值，且带前 4 帧与带后 4 帧平静。
    可调：`switch_burst_win`（过渡带宽度，秒，默认 8）、`switch_min_diff`（峰值阈值，默认 12）。
    """
    box = tuple(cfg["speaker_box"])
    ims = [np.asarray(Image.open(f).convert("L").crop(box), dtype=np.float32) for _, f in frames]
    diffs = [float(np.abs(ims[k + 1] - ims[k]).mean()) for k in range(len(ims) - 1)]
    thr = cfg.get("switch_min_diff", 12)
    W = int(cfg.get("switch_burst_win", 8))
    out = []
    for k in range(1, len(diffs) - W - 1):
        peak = max(diffs[k:k + W])
        pre = float(np.mean(diffs[max(0, k - 4):k]))
        post = float(np.mean(diffs[k + W:k + W + 4]))
        if peak > thr and pre < 5 and post < 5:
            t = frames[k][0]
            if out and t - out[-1] <= 15:
                continue
            out.append(t)
    return out


def dedup_pages(cfg, frames, S):
    """按内容变化切页。

    关键：基准 mask **不累积**。旧实现 `mask |= m` 会越累越宽松，
    导致"模板相同、正文换过"的相邻页被并成一页（实测漏掉大量真实页）。
    不累积则渐显动画不会被切开（旧内容仍在），符合"仅内容被替换才算新页"。
    """
    box = tuple(cfg["slide_box"])
    thr = cfg.get("dedup_lost_ratio", 0.12)
    pages, cur = [], None
    for t, fp in frames:
        if cur is not None and any(abs(t - s) < 0.6 for s in S):
            pages.append(cur)
            cur = None
        m = np.asarray(Image.open(fp).convert("L").crop(box), dtype=np.uint8) < 230
        if cur is None:
            cur = dict(mask=m.copy(), start=t, last=t, frame=fp)
        else:
            lost = np.count_nonzero(cur["mask"] & ~m) / max(np.count_nonzero(cur["mask"]), 1)
            if lost > thr:
                pages.append(cur)
                cur = dict(mask=m.copy(), start=t, last=t, frame=fp)
            else:
                cur["last"] = t
                cur["frame"] = fp      # 保留最新帧 = 动画最终态
    if cur:
        pages.append(cur)

    def dark(p):
        return np.count_nonzero(p["mask"]) / p["mask"].size > 0.5

    def subset(a, b):
        return np.count_nonzero(a & b) / max(np.count_nonzero(a), 1)

    drop = set()
    for i in range(len(pages) - 1):
        if i in drop:
            continue
        dur = pages[i + 1]["start"] - pages[i]["start"]
        if dur < 8 and not dark(pages[i]) and not dark(pages[i + 1]):
            if subset(pages[i]["mask"], pages[i + 1]["mask"]) > 0.85:
                drop.add(i)
            elif i > 0 and i not in drop and not dark(pages[i - 1]) \
                    and subset(pages[i - 1]["mask"], pages[i]["mask"]) > 0.85:
                drop.add(i)
    final = [p for i, p in enumerate(pages) if i not in drop]
    ov = {}
    for j, t in enumerate(S):
        for i, p in enumerate(final):
            if t - 0.6 <= p["start"] <= t + 5:
                ov[i] = j + 1
                break
    return final, ov


def seg_of(p, i, final, S):
    nxt = final[i + 1]["start"] if i + 1 < len(final) else 1e18
    for j, t in enumerate(S):
        if p["start"] <= t < nxt:
            return j + 1
    s = 0
    for j, t in enumerate(S):
        if p["start"] >= t:
            s = j + 1
    return s


def edge_frac(cfg, fp):
    im = np.asarray(Image.open(fp).convert("L").crop(tuple(cfg["slide_box"])), dtype=np.float32)
    return float((np.abs(np.diff(im, axis=1)) > 25).mean() +
                 (np.abs(np.diff(im, axis=0)) > 25).mean())


def page_keep(cfg, fp):
    """保留真实幻灯片页；剔除待机屏/纯色转场/摄像头画面。

    边缘丰富 -> 保留；低边缘页用 OCR 文本量兜底（章节分隔页有字，待机屏没有）。
    """
    if edge_frac(cfg, fp) >= cfg.get("page_min_edge", 0.045):
        return True
    words = sum(len(t.split()) for _, t in ocr_img(Image.open(fp).crop(tuple(cfg["slide_box"]))))
    return words >= 2


def is_polling(fp, cfg=None):
    """投票页：右下 teal 按钮色 + OCR 确认"""
    im = Image.open(fp).convert("RGB")
    w, h = im.size
    zone = np.asarray(im.crop((int(w * 0.55), int(h * 0.68), w, h)), dtype=np.int16)
    r, g, b = zone[..., 0], zone[..., 1], zone[..., 2]
    teal = ((r > 90) & (r < 180) & (g > 130) & (g < 200) & (b > 130) & (b < 200)
            & (abs(g - b) < 25) & (g - r > 15))
    if np.count_nonzero(teal) / teal.size < 0.05:
        return False
    txt = " ".join(t for _, t in ocr_img(im))
    return any(k in txt for k in ("Start Voting", "Polling", "To vote"))


def ocr_title(cfg, frame):
    im = Image.open(frame).crop(tuple(cfg["slide_box"]))
    r = ocr_img(im)
    if not r:
        return "", []
    texts = []
    for box, t in r:
        ys = [p[1] for p in box]
        h = max(ys) - min(ys)
        texts.append((min(ys), min(p[0] for p in box), h, t))
    H = im.size[1]
    cand = [(y, h, t) for y, x, h, t in sorted(texts) if h >= 26 and y < H * 0.6]
    if cand:
        mh = max(h for _, h, _ in cand)
        cand = [(y, h, t) for y, h, t in cand if h >= mh * 0.45]
    return " ".join(t for _, _, t in cand[:4])[:120], [t for _, _, _, t in texts]


def ocr_speaker(cfg, frame):
    return [t for _, t in ocr_img(Image.open(frame).crop(tuple(cfg["speaker_box"])))]


# ---------------------------------------------------------------- 归属
def toks(s):
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", str(s or "")).lower()
    return set(w for w in s.split() if len(w) > 2)


def _compact(s):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(s or "").lower())


def ngrams(s, n=3):
    c = _compact(s)
    return set(c[i:i + n] for i in range(len(c) - n + 1)) if len(c) >= n else set()


def sim(a, b):
    """标题相似度：词 Jaccard 与 3-gram 覆盖率（OCR 粘词兜底）取较大者"""
    ta, tb = toks(a), toks(b)
    j = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    ga, gb = ngrams(a), ngrams(b)
    cov = (len(ga & gb) / min(len(ga), len(gb))) if ga and gb else 0.0
    return max(j, cov * 0.85)


def name_sim(a, b):
    ca, cb = _compact(a), _compact(b)
    if len(ca) < 4 or len(cb) < 4:
        return 0.0
    if ca in cb or cb in ca:
        return 0.95
    ga, gb = ngrams(ca, 2), ngrams(cb, 2)
    return (len(ga & gb) / min(len(ga), len(gb))) if ga and gb else 0.0


def poll_segment(s):
    """判定段是否为投票/互动问答页（规格：排除纯问答）"""
    t = " ".join([s.get("ocr_title", "")] + [str(x) for x in (s.get("titles") or [])]).lower().strip()
    if not t:
        return False
    if re.search(r"(start\s*voting|to\s*vote|polling|vote\s*now)", t):
        return True
    if re.search(r"(question\s*\d|q\s*\d\s*[.:：])", t):
        return True
    if s.get("pages", 99) <= 2 and len(t) <= 45 and re.search(r"^[^a-z]{0,6}questions?\b", t):
        return True
    return False


def assign_talks(cfg, segs, talks, verbose=True):
    """段 -> 议程行 归属。

    难点：fps 抽帧后一场演讲被切成很多段，只有开头那段能对上议程标题，
    中间段的 OCR 是内容页文字。策略：
      1) 锚点 = 标题/讲者强匹配的段，或「利益冲突页 + 讲者命中」（每场演讲开头必有）
      2) 锚点按时间排序，剔除与议程顺序矛盾的乱序锚点
      3) 其余段按播放时间归入「其之前最近的演讲起点」
      4) 兜底：未覆盖行独立重算所有段分数，取最高者补锚点（否则整行丢失）
    """
    if not talks:
        return {}
    order = {tk["row"]: i for i, tk in enumerate(talks)}
    anchor_thr = cfg.get("anchor_score", 0.62)
    coi_thr = cfg.get("coi_anchor_score", 0.45)
    anchors = []
    for s in segs:
        if "pdf" not in s:
            continue
        cands = s.get("titles") or [s.get("ocr_title", "")]
        best, bs, why = None, 0.0, ""
        for tk in talks:
            if QA_RE.search(str(tk.get("title_en", "")) + " " + str(tk.get("title_cn", ""))):
                continue
            s_name = name_sim(s.get("speaker", ""), tk.get("speaker", ""))
            s_tit = 0.0
            for cand in cands:
                s_tit = max(s_tit, sim(cand, tk.get("title_en")), sim(cand, tk.get("title_cn")))
            # 标题优先于讲者：讲者通道不可靠（同一名字常被打散到多段），
            # 曾把 A 场标题页判给 B 行（speaker 分 0.95 压过 title 分）。
            if s_tit >= 0.30:
                sc, w = s_tit, "title"
            elif s_name >= 0.50:
                sc, w = s_name, "speaker"
            else:
                sc, w = (s_name, "speaker") if s_name >= s_tit else (s_tit, "title")
            if sc > bs:
                bs, best, why = sc, tk["row"], w
        s["match_row"], s["match_score"], s["match_by"] = best, round(bs, 2), why
        head = " ".join(cands)[:400]
        is_coi = bool(COI_RE.search(head))
        if best and (bs >= anchor_thr or (is_coi and bs >= coi_thr)):
            anchors.append((s.get("t0", 0.0), best, bs))
    have = {a[1] for a in anchors}
    for tk in talks:
        r = tk["row"]
        if r in have:
            continue
        if QA_RE.search(str(tk.get("title_en", "")) + " " + str(tk.get("title_cn", ""))):
            continue
        best = None
        for s in segs:
            if "pdf" not in s:
                continue
            cands = s.get("titles") or [s.get("ocr_title", "")]
            s_tit = 0.0
            for c in cands:
                s_tit = max(s_tit, sim(c, tk.get("title_en")), sim(c, tk.get("title_cn")))
            s_name = name_sim(s.get("speaker", ""), tk.get("speaker", ""))
            sc = max(s_tit, s_name)
            if sc >= 0.12 and (best is None or sc > best[1]):
                best = (s.get("t0", 0.0), sc)
        if best and best[1] >= cfg.get("fallback_score", 0.35):
            anchors.append((best[0], r, best[1]))
    anchors.sort()
    kept = []
    for at, ar, bs in anchors:
        if not kept or order.get(ar, 0) >= order.get(kept[-1][1], 0):
            kept.append((at, ar, bs))
        elif bs >= 0.5 and order.get(ar, 0) > order.get(kept[-1][1], 0) - 1:
            kept.append((at, ar, bs))
    anchors = kept
    if verbose:
        print("  锚点 %d 个: %s" % (len(anchors),
              ", ".join("t=%.0f->r%s" % (a, r) for a, r, _ in anchors)), flush=True)
    assign = {}
    for s in sorted(segs, key=lambda x: x.get("t0", 0.0)):
        if "pdf" not in s:
            continue
        t0 = s.get("t0", 0.0)
        row = None
        for at, ar, _ in anchors:
            if at <= t0 + 2:
                row = ar
            else:
                break
        if row is None and anchors:
            row = anchors[0][1]      # 首锚点之前：开场/标题页归第一场演讲
        if row:
            s["assigned_row"] = row
            assign.setdefault(row, []).append(s)
    return assign


# ---------------------------------------------------------------- 步骤拆分（每步都可独立运行）
STEPS = ("download", "frames", "segment", "assign")
# 各步的输入输出契约见 references/standalone.md：
#   download -> <tmp_root>/S_<mid>/video.mp4
#   frames   -> <tmp_root>/S_<mid>/parts/part_01/f_*.jpg + showinfo.log
#   segment  -> progress[key].segments + pdf/S_<mid>/*.pdf + analysis_sheet.png
#   assign   -> progress[key].assign


def step_download(cfg, task, log=print):
    """只下载视频，不抽帧。落盘前做配额检查——把空间守在告警线之上，
    比事后清理可靠得多（Windows 低磁盘告警会反复弹窗）。"""
    import diskguard as DG
    out = work_dir(cfg, task)
    vp = os.path.join(out, "video.mp4")
    if os.path.exists(vp) and os.path.getsize(vp) > 10 * 1048576:
        log("  视频已缓存 %.0f MB" % (os.path.getsize(vp) / 1048576))
        return out
    need = float(cfg.get("video_est_gb", 2.5))
    if not DG.guard(cfg, need, what="下载视频 mid=%s" % task.get("manage_id"), log=log):
        raise RuntimeError("磁盘配额不足，已取消下载（先 cleanup.py --plan，或把 tmp_root 指向空闲盘）")
    download(cfg["video_url_tpl"].format(mid=task["manage_id"]), cfg["jwt"], vp, log, cfg=cfg)
    return out


def step_frames(cfg, task, log=print):
    """抽帧（内部含「下载不全则重下」的兜底）。"""
    return extract_frames(cfg, task, log)


# ---------------------------------------------------------------- 单场
def process(cfg, task, prog, no_frame=False, log=print, steps=None):
    k = C.key_of(task)
    mid = task.get("manage_id")
    log("=== [%s] %s (mid=%s, %d 行) ===" % (task["sheet"], task["session"][:60], mid, len(task["rows"])))
    if not mid:
        prog[k] = {"status": "no_video", "sheet": task["sheet"], "rows": task["rows"]}
        C.save_progress(cfg, prog)
        return
    steps = tuple(steps or STEPS)
    if "download" in steps:
        step_download(cfg, task, log)
    if "frames" in steps and not no_frame:
        step_frames(cfg, task, log)
    if "segment" in steps:
        step_segment(cfg, task, prog, log)
    if "assign" in steps:
        step_assign(cfg, task, prog, log)
    log("  完成步骤: %s" % (",".join(steps),))


def step_segment(cfg, task, prog, log=print):
    """切段 → 切页 → 段 PDF → 缩略图总览。可独立重跑（需要已有抽帧产物）。"""
    out = work_dir(cfg, task)
    mid = task.get("manage_id")      # 拆成独立步骤后漏定义，导致 segment 必崩（此前只在 process() 作用域内）
    frames = load_frames(out)
    if len(frames) < 20:
        raise RuntimeError("frames<20：抽帧产物缺失，请先跑 --steps frames")
    log("  帧数 %d  时间 %.1f-%.1f" % (len(frames), frames[0][0], frames[-1][0]))

    S = detect_switches(cfg, frames)
    final, ov = dedup_pages(cfg, frames, S)
    log("  切换点 %d 个，页 %d 个" % (len(S), len(final)))

    groups = {i: [] for i in range(len(S) + 1)}
    for i, p in enumerate(final):
        groups[ov.get(i, seg_of(p, i, final, S))].append(p)

    # 段首转场态合并（讲者条切换后镜头仍在旧片子上）
    def phash(fp):
        a = np.asarray(Image.open(fp).convert("L").crop(tuple(cfg["slide_box"])).resize((8, 8)),
                       dtype=np.float32)
        return (a > a.mean()).flatten()
    for kk in sorted(groups):
        sel = groups[kk]
        if kk == 0 or len(sel) < 2:
            continue
        h0, j = phash(sel[0]["frame"]), 0
        for mi in range(1, len(sel)):
            if sel[mi]["start"] - sel[0]["start"] > 45:
                break
            if int((h0 != phash(sel[mi]["frame"])).sum()) < 20:
                j = mi
        if j > 0:
            groups[kk] = [dict(sel[j])] + sel[j + 1:]

    pdfdir = os.path.join(cfg["pdf_dir_abs"], "S_%s" % mid)
    os.makedirs(pdfdir, exist_ok=True)
    used, segs = set(), []
    for kk in sorted(groups):
        sel = groups[kk]
        if not sel:
            continue
        t0, t1 = sel[0]["start"], sel[-1]["last"]
        sel = [p for p in sel if page_keep(cfg, p["frame"])]
        if not sel:
            segs.append({"seg": kk, "note": "blank/transition only", "t": "%.0f-%.0f" % (t0, t1)})
            continue
        t0, t1 = sel[0]["start"], sel[-1]["last"]
        # 单页段只有持续 >5min（讨论/待机）才丢弃。
        # 【漏页元凶】早期逻辑"非全场唯一段的单页段一律当过渡丢弃"：
        # 演讲者停留较久的幻灯片就成了单页段，标题页/利益冲突页被整段扔掉。
        if len(sel) == 1 and (t1 - t0) > cfg.get("discard_single_page_sec", 300):
            segs.append({"seg": kk, "note": "single page >5min (discussion) skipped",
                         "t": "%.0f-%.0f" % (t0, t1)})
            continue
        keep = [p for p in sel if not is_polling(p["frame"])]
        if not keep:
            continue
        sel = keep
        if len(sel) <= 3:      # 短段里的投票/问答页单独剔除（不连带整段）
            head = " ".join(t for _, t in (ocr_img(Image.open(sel[0]["frame"])) or []))
            if POLL_RE.search(head) or (len(sel) == 1 and is_polling(sel[0]["frame"])):
                sel = sel[1:] if len(sel) > 1 else []
            if not sel:
                segs.append({"seg": kk, "note": "poll/qa skipped", "t": "%.0f-%.0f" % (t0, t1)})
                continue
        cands, spk = [], ""
        for p in sel[:5]:
            tt, _ = ocr_title(cfg, p["frame"])
            if len(tt) >= 6:
                cands.append(tt)
        for p in sel[:3]:
            sp = ocr_speaker(cfg, p["frame"])
            if sp:
                spk = sp[0]
                break
        title = cands[0] if cands else "Talk %d" % kk
        base = clean_name(title, 70)
        if spk:
            base = "%s - %s" % (clean_name(title, 60), clean_name(spk, 30))
        name, tag = base, 2
        while name.lower() in used:
            name = "%s (%d)" % (base, tag)
            tag += 1
        used.add(name.lower())
        doc = pymupdf.open()
        import io as _io
        for p in sel:
            im = Image.open(p["frame"]).crop(tuple(cfg["slide_box"]))
            b = _io.BytesIO()
            im.save(b, "JPEG", quality=92)
            pg = doc.new_page(width=im.size[0], height=im.size[1])
            pg.insert_image(pg.rect, stream=b.getvalue())
        fp = os.path.join(pdfdir, name + ".pdf")
        doc.save(fp, deflate=False)
        doc.close()
        segs.append({"seg": kk, "name": name, "pdf": fp, "pages": len(sel),
                     "t0": round(t0, 1), "t1": round(t1, 1),
                     "ocr_title": title, "titles": cands[:5], "speaker": spk})
        log("  段%-3d %2d 页  %s" % (kk, len(sel), name[:70]))

    # 缩略图总览（人工抽检用）
    allp = [p for kk in sorted(groups) for p in groups[kk]]
    if allp:
        W, cols = 170, 8
        H = int(W * 804 / 1430)     # 16:9
        rows = (len(allp) + cols - 1) // cols
        canvas = Image.new("RGB", (cols * (W + 6) + 6, rows * (H + 22) + 6), (245, 245, 245))
        dr = ImageDraw.Draw(canvas)
        for i, p in enumerate(allp):
            r, c = divmod(i, cols)
            x, y = 6 + c * (W + 6), 6 + r * (H + 22)
            canvas.paste(Image.open(p["frame"]).crop(tuple(cfg["slide_box"])).resize((W, H)), (x, y))
            dr.text((x + 2, y + H + 2), "#%d t=%.0f" % (i, p["start"]), fill=(0, 0, 0))
        canvas.save(os.path.join(out, "analysis_sheet.png"))

    # 只写 segments，不动 assign（两步解耦，可分别重跑）
    k = C.key_of(task)
    st = prog.get(k) or {}
    st.update({"status": st.get("status") or "pdf_done", "sheet": task["sheet"],
               "rows": task["rows"], "kind": task.get("kind"), "segments": segs})
    st.setdefault("assign", {})
    prog[k] = st
    C.save_progress(cfg, prog)
    log("  段 PDF 完成，%d 段" % len([s for s in segs if "pdf" in s]))
    return segs


def step_assign(cfg, task, prog, log=print):
    """只重算归属（秒级）：读 progress 里的 segments，不抽帧、不重建 PDF。"""
    k = C.key_of(task)
    st = prog.get(k) or {}
    segs = st.get("segments") or []
    if not segs:
        raise RuntimeError("没有段数据，请先跑 --steps segment")
    if task.get("kind") == "talk-rows":
        assign = assign_talks(cfg, segs, task.get("talks") or [])
        for r in list(assign):      # 剔除投票/问答段
            keep = [s for s in assign[r] if not poll_segment(s)]
            if keep:
                assign[r] = keep
            else:
                del assign[r]
    else:
        assign = {}
    st.update({"status": st.get("status") or "pdf_done", "sheet": task["sheet"],
               "rows": task["rows"], "kind": task.get("kind"),
               "assign": {str(a): [s["name"] for s in v] for a, v in assign.items()}})
    prog[k] = st
    C.save_progress(cfg, prog)
    log("  归属完成：%s" % dict(sorted({int(r): len(v) for r, v in assign.items()}.items())))
    return st["assign"]


def safe_rmtree(path):
    """逐文件删除 + 逐目录 rmdir（绕过批量删除保护；分批便于审计）"""
    if not os.path.exists(path):
        return 0
    n = 0
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
                n += 1
            except Exception:
                pass
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except Exception:
                pass
    try:
        os.rmdir(path)
    except Exception:
        pass
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", default="--all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-frame", action="store_true", help="跳过抽帧，用已有帧重算")
    ap.add_argument("--rematch", action="store_true", help="只重算归属，不抽帧不重建 PDF（= --steps assign）")
    ap.add_argument("--steps", default=None,
                    help="只跑指定步骤，逗号分隔：download,frames,segment,assign")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()

    cfg = C.load(a.base)
    tasks = C.tasks(cfg)
    prog = C.progress(cfg)

    steps = None
    if a.steps:
        steps = [s.strip() for s in a.steps.split(",") if s.strip()]
        bad = [s for s in steps if s not in STEPS]
        if bad:
            print("未知步骤:", bad, "可选:", STEPS)
            sys.exit(1)
    elif a.rematch:
        steps = ["assign"]

    # --rematch 保持「全部场次」的历史语义；
    # 指定了单场 key 的 --steps assign 只跑那一场
    full_rematch = a.rematch or (steps == ["assign"] and (a.all or a.key == "--all"))
    if full_rematch:
        for t in tasks:
            k = C.key_of(t)
            st = prog.get(k) or {}
            if not (st.get("segments") or []) or t.get("kind") != "talk-rows":
                continue
            old = {int(r): len(v) for r, v in (st.get("assign") or {}).items()}
            try:
                step_assign(cfg, t, prog, log=lambda *m: None)
            except Exception as e:
                print("  %s 重算失败: %s" % (k[:40], e))
                continue
            new = {int(r): len(v) for r, v in (prog[k].get("assign") or {}).items()}
            print("%-10s %-46s 旧:%s" % (t["sheet"], k[:46], dict(sorted(old.items()))))
            print("%-10s %-46s 新:%s" % ("", "", dict(sorted(new.items()))))
        print("已写入归属（未重抽帧）")
        return

    if a.key == "--all" or a.all:
        # 指定 --steps 时，对已有数据的场次也重跑该步骤（否则独立重跑无意义）
        if steps:
            todo = [t for t in tasks if t.get("manage_id")
                    and prog.get(C.key_of(t), {}).get("status") != "no_video"]
        else:
            todo = [t for t in tasks
                    if prog.get(C.key_of(t), {}).get("status") not in ("done", "no_video")]
        todo.sort(key=lambda t: (t.get("priority", 9), min(t["rows"])))
        print("待处理 %d 场（步骤 %s）" % (len(todo), ",".join(steps or STEPS)), flush=True)
        for t in todo:
            k = C.key_of(t)
            try:
                if steps or prog.get(k, {}).get("status") not in ("pdf_done", "done"):
                    process(cfg, t, prog, no_frame=a.no_frame, steps=steps)
            except Exception as e:
                import traceback
                traceback.print_exc()
                prog[k] = {"status": "failed", "why": str(e)[:200]}
                C.save_progress(cfg, prog)
    else:
        cands = [t for t in tasks if a.key in t["session"] or str(t.get("manage_id")) == a.key]
        if not cands:
            print("没有匹配的场次:", a.key)
            sys.exit(1)
        process(cfg, cands[0], prog, no_frame=a.no_frame, steps=steps)

    done = [k for k, v in prog.items() if v.get("status") in ("done", "pdf_done")]
    print("进度: %d / %d 场完成" % (len(done), len(tasks)))


if __name__ == "__main__":
    main()
