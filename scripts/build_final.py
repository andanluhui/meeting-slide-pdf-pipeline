# -*- coding: utf-8 -*-
"""按「议程行」合并段 PDF -> 成品（pdf_final/<子表>/<行号>_<标题> - <讲者>.pdf）

- talk-rows（学术子表）：每行 = 一场演讲 -> 一份 PDF
- session-row / replay-row（企业专场、回放补充）：整场 -> 一份 PDF
- 合并顺序按播放时间 t0；逐页 pHash 全局去重（保留首次出现）

可独立运行，且支持细到「单场 / 单行」重做：
  python build_final.py                       全量重建
  python build_final.py 1003                  只重建某个子表
  python build_final.py --session 6742        只重建某场（manage_id 或 session 名片段）
  python build_final.py 1003 --rows 121,122   只重建指定行
"""
import os
import io
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 解释器自举：当前 python 缺依赖时，自动换成装了依赖的解释器重新执行本脚本。
# 必须放在 pymupdf / PIL 之前（详见 pybootstrap.py）。
import pybootstrap
pybootstrap.ensure(pybootstrap.DEPS_PDF)

import pymupdf
from PIL import Image

import config as C


def phash(pix_bytes):
    im = Image.open(io.BytesIO(pix_bytes)).convert("L").resize((16, 16))
    px = list(im.getdata())
    avg = sum(px) / len(px)
    return "".join("1" if v > avg else "0" for v in px)


def similar(a, b, maxd=6):
    return sum(1 for x, y in zip(a, b) if x != y) <= maxd


def dedup_pages(doc):
    seen, keep = [], []
    for i in range(doc.page_count):
        h = phash(doc[i].get_pixmap(dpi=60).tobytes("png"))
        if any(similar(h, h2) for h2 in seen):
            continue
        seen.append(h)
        keep.append(i)
    return keep


def clean(s, maxlen=90):
    import re
    s = re.sub(r"[:/\\?*\"<>|\n\r]+", " ", str(s or ""))
    return re.sub(r"\s+", " ", s).strip().strip(".")[:maxlen]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", nargs="?", default=None, help="只重建该子表")
    ap.add_argument("--session", default=None, help="只重建该场（manage_id 或 session 名片段）")
    ap.add_argument("--rows", default=None, help="只重建这些行，逗号分隔，如 121,122")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()

    cfg = C.load(a.base)
    tasks = C.tasks(cfg)
    prog = C.progress(cfg)
    out_root = cfg["final_dir_abs"]
    only_rows = None
    if a.rows:
        only_rows = set()
        for x in a.rows.split(","):
            x = x.strip()
            if x:
                only_rows.add(int(x))

    # 只跑一部分时，报告按「合并到已有报告」处理，避免覆盖全量结果
    partial = bool(a.sheet or a.session or only_rows)
    report = []
    old_report = []
    if partial and os.path.exists(cfg["final_report_abs"]):
        try:
            old_report = json.load(open(cfg["final_report_abs"], encoding="utf-8"))
        except Exception:
            old_report = []

    for t in tasks:
        k = C.key_of(t)
        st = prog.get(k, {})
        if st.get("status") not in ("done", "pdf_done"):
            continue
        sheet = t["sheet"]
        if a.sheet and sheet != a.sheet:
            continue
        if a.session and (a.session not in t["session"]
                          and str(t.get("manage_id")) != a.session):
            continue
        outdir = os.path.join(out_root, sheet)
        os.makedirs(outdir, exist_ok=True)
        segs = [s for s in st.get("segments", []) if s.get("pdf") and os.path.exists(s["pdf"])]

        if t.get("kind") == "talk-rows":
            talks = {int(tk["row"]): tk for tk in (t.get("talks") or [])}
            groups = []
            for row, names in (st.get("assign") or {}).items():
                items = sorted([s for s in segs if s["name"] in names], key=lambda s: s.get("t0", 0))
                if items:
                    groups.append((int(row), talks.get(int(row)), items))
            # 未匹配段不并入任何演讲，避免错行污染
        else:
            groups = [(int(t["rows"][0]), None, sorted(segs, key=lambda s: s.get("t0", 0)))]

        for row, tk, items in groups:
            if only_rows is not None and row not in only_rows:
                continue
            if tk:
                title = clean(tk.get("title_en") or tk.get("title_cn") or "", 80)
                spk = clean(tk.get("speaker") or "", 30)
                fname = ("%d_%s - %s.pdf" % (row, title, spk)) if spk else ("%d_%s.pdf" % (row, title))
            else:
                fname = "%d_%s.pdf" % (row, clean(t["session"], 70))
            out_pdf = os.path.join(outdir, fname)
            doc = pymupdf.open()
            total = 0
            for s in items:
                src = pymupdf.open(s["pdf"])
                total += src.page_count
                # 【必须无条件重建】按"文件已存在就跳过"会导致重跑后整行丢失（实测丢 31 行）
                doc.insert_pdf(src)
                src.close()
            if doc.page_count == 0:
                doc.close()
                continue
            # 唯一临时名：固定名在上一轮进程被强杀后会残留文件锁
            tmp = os.path.join(cfg["base"], "_work", "_merge_tmp_%d.pdf" % os.getpid())
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            doc.save(tmp, deflate=False)
            doc.close()
            d2 = pymupdf.open(tmp)
            idx = dedup_pages(d2)
            out = pymupdf.open()
            for i in idx:
                out.insert_pdf(d2, from_page=i, to_page=i)
            out.save(out_pdf, deflate=False)
            npages = out.page_count
            out.close()
            d2.close()
            try:
                os.remove(tmp)
            except OSError:
                pass
            report.append((sheet, row, fname, len(items), total, npages))
            print("[%s] r%-4d 段=%-2d 原始=%-3d -> %-3d  %s" % (sheet, row, len(items), total, npages, fname[:60]))

    n_new = len(report)
    if partial:
        # 局部重做：用新结果覆盖同 (sheet,row)，其余沿用旧报告，避免把全量结果冲掉
        keys = {(e[0], e[1]) for e in report}
        merged = [e for e in old_report if (e[0], e[1]) not in keys] + report
        merged.sort(key=lambda e: (str(e[0]), e[1]))
        report = merged
    json.dump(report, open(cfg["final_report_abs"], "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("完成 %d 行（本次重建 %d 行）-> %s" % (len(report), n_new, cfg["final_report_abs"]))


if __name__ == "__main__":
    main()
