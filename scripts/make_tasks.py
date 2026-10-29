# -*- coding: utf-8 -*-
"""从飞书日程表生成 tasks.json 骨架（主线第 1 步：建立底账）。

思路：表格里一行 = 一场演讲（talk-rows）或一整场（session-row）。
本脚本按关键词筛出目标主题的行，聚合成「场次」任务，未匹配到视频平台的场次
manage_id 留空，需人工或用平台检索补齐。

用法:
  python make_tasks.py --sheet 1001 --kw acne,rosacea --out _work/tasks.json
  python make_tasks.py --sheet 1001 --kw acne --col-title 3 --col-speaker 5
（列号从 1 开始；默认自动探测含 title/speaker 语义的表头）
"""
import os
import re
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
from colmap import Sheets


def pick_col(hdr, cands):
    for name, col in hdr.items():
        low = name.lower()
        if any(c in low for c in cands):
            return col
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True, help="子表名（config.sheets 里的键）")
    ap.add_argument("--kw", default="", help="主题关键词，逗号分隔（小写匹配）")
    ap.add_argument("--start-row", type=int, default=2)
    ap.add_argument("--end-row", type=int, default=600)
    ap.add_argument("--out", default=None)
    ap.add_argument("--base", default=None)
    a = ap.parse_args()

    cfg = C.load(a.base)
    sh = Sheets(cfg)
    sid = cfg["sheets"][a.sheet]
    hdr = sh.header_map(sid)
    col_title = pick_col(hdr, ["title", "主题", "题目"]) or "C"
    col_spk = pick_col(hdr, ["speaker", "讲者", "讲者/"]) or None
    col_sess = pick_col(hdr, ["session", "专场", "会议"]) or None
    print("列定位: 标题=%s 讲者=%s 场次=%s" % (col_title, col_spk, col_sess))

    kws = [k.strip().lower() for k in a.kw.split(",") if k.strip()]
    rows_txt = sh.col_texts(sid, col_title, nrows=a.end_row)
    sess_txt = sh.col_texts(sid, col_sess, nrows=a.end_row) if col_sess else {}
    spk_txt = sh.col_texts(sid, col_spk, nrows=a.end_row) if col_spk else {}

    tasks = []
    cur = None
    for r in range(a.start_row, a.end_row + 1):
        title = rows_txt.get(r, "").strip()
        if not title:
            continue
        sess = sess_txt.get(r, "").strip()
        spk = spk_txt.get(r, "").strip()
        if kws and not any(k in (title + " " + sess).lower() for k in kws):
            continue
        if sess:
            key = sess[:60]
            if not cur or cur["session"] != key:
                cur = {"sheet": a.sheet, "session": key, "rows": [], "kind": "talk-rows",
                       "manage_id": None, "priority": 2, "talks": []}
                tasks.append(cur)
        else:
            if not cur:
                cur = {"sheet": a.sheet, "session": "未命名场次", "rows": [], "kind": "session-row",
                       "manage_id": None, "priority": 2, "talks": []}
                tasks.append(cur)
        cur["rows"].append(r)
        cur["talks"].append({"row": r, "title_en": title, "title_cn": "", "speaker": spk})

    out = a.out or os.path.join(cfg["base"], "_work", "tasks.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(tasks, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写出 %d 个场次任务 -> %s" % (len(tasks), out))
    print("下一步：为每场补 manage_id（视频平台 ID），必要时手工校正 kind/priority。")


if __name__ == "__main__":
    main()
